#!/usr/bin/env python3
"""Generate the ENTSO-E market dashboard from live API data or bundled demo CSVs."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from entsoe import EntsoePandasClient
from entsoe.exceptions import NoMatchingDataError
from entsoe.mappings import Area, NEIGHBOURS, lookup_area
from plotly.subplots import make_subplots

ROOT_DIR = Path(__file__).resolve().parent
NOTEBOOK_DIR = ROOT_DIR / "notebook"
DEMO_FILES = {
	"day_ahead_prices": "demo_day_ahead_prices.csv",
	"generation": "demo_generation.csv",
	"generation_forecast": "demo_generation_forecast.csv",
	"load": "demo_load.csv",
	"net_position": "demo_net_position.csv",
	"crossborder_flows": "demo_crossborder_flows.csv",
}
DEMO_COUNTRY_CODE = "DK_1"
DEMO_DESTINATION_ZONE = "DE_LU"


def find_api_token() -> str:
	"""Return the ENTSO-E API token, if present."""
	candidates = [
		ROOT_DIR / "entsoe-api.txt",
		NOTEBOOK_DIR / "entsoe-api.txt",
		Path("entsoe-api.txt"),
	]

	for candidate in candidates:
		if candidate.exists():
			token = candidate.read_text(encoding="utf-8").strip()
			if token and "<YOUR" not in token:
				return token
	return ""


def load_demo_data() -> dict[str, pd.Series | pd.DataFrame]:
	"""Load the offline sample CSV files bundled in the notebook folder."""
	results: dict[str, pd.Series | pd.DataFrame] = {}
	search_dirs = [NOTEBOOK_DIR, ROOT_DIR, Path.cwd() / "notebook", Path.cwd()]

	for name, filename in DEMO_FILES.items():
		candidate_paths = [directory / filename for directory in search_dirs]
		csv_path = next((path for path in candidate_paths if path.is_file()), None)
		if csv_path is None:
			checked_paths = ", ".join(str(path) for path in candidate_paths)
			print(f"❌ Demo file missing: '{filename}'. Checked: {checked_paths}")
			continue

		frame = pd.read_csv(csv_path, index_col=0, parse_dates=True)
		if name in {"day_ahead_prices", "generation_forecast", "load", "net_position", "crossborder_flows"} and isinstance(frame, pd.DataFrame):
			results[name] = frame.iloc[:, 0]
		else:
			results[name] = frame

	return results


def get_data_period(data_results: dict[str, Any]) -> tuple[pd.Timestamp, pd.Timestamp]:
	"""Return the actual timestamp bounds available in the loaded CSV datasets."""
	indexes = [data.index for data in data_results.values() if not data.empty]
	if not indexes:
		raise ValueError("No demo data is available to determine a date range.")

	return min(index.min() for index in indexes), max(index.max() for index in indexes)


def query_live_data(country_code: str, destination_zone: str, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, Any]:
	"""Fetch all required ENTSO-E datasets for a chosen bidding zone and date range."""
	token = find_api_token()
	if not token:
		raise FileNotFoundError("No API token was found. Create entsoe-api.txt or use --demo.")

	client = EntsoePandasClient(api_key=token)
	local_tz = lookup_area(country_code).tz
	results: dict[str, Any] = {}

	def safe_query(name: str, func: Any, *args: Any, **kwargs: Any) -> None:
		try:
			results[name] = func(*args, **kwargs)
		except NoMatchingDataError:
			print(f"❌ No matching data found for: {name}")
		except Exception as exc:  # pragma: no cover - depends on external API response
			print(f"⚠️ Error fetching {name}: {exc}")

	start_ts = pd.Timestamp(start)
	end_ts = pd.Timestamp(end)
	if start_ts.tzinfo is None:
		start_ts = start_ts.tz_localize(local_tz)
	if end_ts.tzinfo is None:
		end_ts = end_ts.tz_localize(local_tz)

	safe_query("day_ahead_prices", client.query_day_ahead_prices, country_code, start=start_ts, end=end_ts)
	safe_query("generation", client.query_generation, country_code, start=start_ts, end=end_ts, psr_type=None)
	safe_query("generation_forecast", client.query_generation_forecast, country_code, start=start_ts, end=end_ts)
	safe_query("load", client.query_load, country_code, start=start_ts, end=end_ts)
	safe_query("net_position", client.query_net_position, country_code, start=start_ts, end=end_ts)
	safe_query("crossborder_flows", client.query_crossborder_flows, country_code, destination_zone, start=start_ts, end=end_ts)
	return results


def build_generation_groups(df_generation: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
	"""Aggregate generation columns into the three merit-order buckets."""
	merit_1_all = [
		"Solar",
		"Wind Onshore",
		"Wind Offshore",
		"Hydro Run-of-river and pondage",
		"Marine",
		"Nuclear",
		"Geothermal",
	]
	merit_2_all = [
		"Biomass",
		"Waste",
		"Other renewable",
		"Hydro Water Reservoir",
		"Hydro Pumped Storage",
		"Energy storage",
	]
	merit_3_all = [
		"Fossil Gas",
		"Fossil Hard coal",
		"Fossil Brown coal/Lignite",
		"Fossil Coal-derived gas",
		"Fossil Oil",
		"Fossil Oil shale",
		"Fossil Peat",
		"Other",
	]

	available_cols = [column for column in df_generation.columns if "Consumption" not in column]
	m1_existing = [column for column in merit_1_all if column in available_cols]
	m2_existing = [column for column in merit_2_all if column in available_cols]
	m3_existing = [column for column in merit_3_all if column in available_cols]

	actual_m1 = df_generation[m1_existing].sum(axis=1) if m1_existing else pd.Series(0, index=df_generation.index)
	actual_m2 = df_generation[m2_existing].sum(axis=1) if m2_existing else pd.Series(0, index=df_generation.index)
	actual_m3 = df_generation[m3_existing].sum(axis=1) if m3_existing else pd.Series(0, index=df_generation.index)
	actual_total = df_generation[available_cols].sum(axis=1)
	return actual_m1, actual_m2, actual_m3, actual_total


def prompt_for_origin_zone() -> tuple[str, str]:
	"""Prompt the user to choose an origin bidding zone from the available ENTSO-E list."""
	all_zones = sorted(area.name for area in Area)
	print("\nAvailable Origin Bidding Zones:")
	print(", ".join(all_zones))
	while True:
		zone = input("\nEnter origin bidding zone (e.g., DK_1 or DE): ").strip().upper()
		if zone in {item.upper() for item in all_zones}:
			return zone, lookup_area(zone).tz
		print(f"❌ Invalid bidding zone: '{zone}'. Please choose one of the listed values.")


def prompt_for_destination_zone(origin_zone: str) -> str:
	"""Prompt the user to choose a destination bidding zone from the valid neighbours list."""
	possible_neighbors = sorted(NEIGHBOURS.get(origin_zone, []))
	print(f"\nPossible destination zones for {origin_zone}:")
	print(", ".join(possible_neighbors) if possible_neighbors else "No neighbour zones found for this bidding zone.")
	while True:
		zone = input("\nEnter destination bidding zone: ").strip().upper()
		if zone and (not possible_neighbors or zone in {item.upper() for item in possible_neighbors}):
			return zone
		print(f"❌ Invalid destination zone: '{zone}'. Please choose one of the listed values.")


def prompt_for_date_range(local_tz: str) -> tuple[pd.Timestamp, pd.Timestamp]:
	"""Prompt the user to provide a start and end date in YYYYMMDD format."""
	print("\nEnter dates using pattern YYYYMMDD (e.g., 20260920)")
	while True:
		start_text = input("START date: ").strip()
		end_text = input("END date: ").strip()
		try:
			start = pd.Timestamp(start_text, tz=local_tz)
			end = pd.Timestamp(end_text, tz=local_tz)
			if end >= start:
				return start, end
			print("❌ End date must be greater than or equal to the start date.")
		except Exception:
			print("❌ Invalid dates. Please use YYYYMMDD format, for example 20260920.")


def build_dashboard(data_results: dict[str, Any], country_code: str, destination_zone: str, local_tz: str) -> go.Figure:
	"""Create the Plotly dashboard."""
	df_price = data_results.get("day_ahead_prices")
	df_generation = data_results.get("generation")
	df_forecast = data_results.get("generation_forecast")
	df_load = data_results.get("load")
	df_net_pos = data_results.get("net_position")
	df_flows = data_results.get("crossborder_flows")

	if df_price is None or df_generation is None:
		raise ValueError("Generation or price data is missing for the selected period.")

	actual_m1, actual_m2, actual_m3, actual_total = build_generation_groups(df_generation)
	forecast_total = df_forecast.sum(axis=1) if isinstance(df_forecast, pd.DataFrame) else df_forecast
	load_series = df_load.iloc[:, 0] if isinstance(df_load, pd.DataFrame) else df_load
	net_pos_series = df_net_pos.iloc[:, 0] if isinstance(df_net_pos, pd.DataFrame) else df_net_pos
	flow_series = df_flows.iloc[:, 0] if isinstance(df_flows, pd.DataFrame) else df_flows

	fig = make_subplots(specs=[[{"secondary_y": True}]])

	if load_series is not None:
		fig.add_trace(
			go.Scatter(
				x=load_series.index,
				y=load_series.values,
				name="Actual Load (Demand)",
				line=dict(color="#333333", width=2.5),
			),
			secondary_y=False,
		)

	fig.add_trace(
		go.Scatter(
			x=actual_total.index,
			y=actual_total.values,
			name="Total Actual Generation",
			line=dict(color="#000080", width=3),
		),
		secondary_y=False,
	)

	if forecast_total is not None:
		fig.add_trace(
			go.Scatter(
				x=forecast_total.index,
				y=forecast_total.values,
				name="Total Generation Forecast",
				line=dict(color="#7f7fcc", width=2, dash="dash"),
			),
			secondary_y=False,
		)

	if net_pos_series is not None:
		fig.add_trace(
			go.Scatter(
				x=net_pos_series.index,
				y=net_pos_series.values,
				name="Net Position (Export/Import)",
				line=dict(color="#17becf", width=1.5, dash="dot"),
			),
			secondary_y=False,
		)

	if flow_series is not None:
		fig.add_trace(
			go.Scatter(
				x=flow_series.index,
				y=flow_series.values,
				name=f"Flow: {country_code} ➔ {destination_zone}",
				line=dict(color="#db5f8e", width=2, dash="dashdot"),
			),
			secondary_y=False,
		)

	fig.add_trace(
		go.Scatter(
			x=actual_m1.index,
			y=actual_m1.values,
			name="1. Variable RES/Nuclear",
			line=dict(color="#2ca02c", width=1.5),
		),
		secondary_y=False,
	)
	fig.add_trace(
		go.Scatter(
			x=actual_m2.index,
			y=actual_m2.values,
			name="2. Biomass/Waste/Storage",
			line=dict(color="#9467bd", width=1.5),
		),
		secondary_y=False,
	)
	fig.add_trace(
		go.Scatter(
			x=actual_m3.index,
			y=actual_m3.values,
			name="3. Fossil Fuels/Other",
			line=dict(color="#d62728", width=1.5),
		),
		secondary_y=False,
	)

	fig.add_trace(
		go.Scatter(
			x=df_price.index,
			y=df_price.values,
			name="Day-Ahead Price",
			line=dict(color="#ff7f0e", width=2.5),
			hovertemplate="Price: %{y:,.2f} EUR/MWh",
		),
		secondary_y=True,
	)

	period_start, period_end = get_data_period(data_results)
	fig.update_layout(
		title=dict(
			text=(
				f"<b>Interactive Grid Analysis: Zone {country_code} | Flow to {destination_zone}"
				f" | {period_start:%Y-%m-%d} to {period_end:%Y-%m-%d}</b>"
			),
			x=0.5,
			font=dict(size=16),
		),
		xaxis_title=f"Timestamp ({local_tz})",
		hovermode="x unified",
		legend=dict(x=0.01, y=0.99, bgcolor="rgba(255, 255, 255, 0.7)", font=dict(size=10)),
		width=1300,
		height=800,
	)
	fig.update_yaxes(title_text="Electricity Volume (MW)", secondary_y=False, gridcolor="rgba(0,0,0,0.05)")
	fig.update_yaxes(title_text="Day-Ahead Price (EUR/MWh)", secondary_y=True)
	return fig


def main() -> None:
	parser = argparse.ArgumentParser(description="Generate the ENTSO-E market dashboard.")
	parser.add_argument("--country-code", help="Origin bidding zone, e.g. DK_1 or DE.")
	parser.add_argument("--destination-zone", help="Destination bidding zone for cross-border flows.")
	parser.add_argument("--start", help="Start date as YYYYMMDD or ISO date.")
	parser.add_argument("--end", help="End date as YYYYMMDD or ISO date.")
	parser.add_argument("--demo", action="store_true", help="Use the bundled CSV sample instead of the live API.")
	parser.add_argument("--output", help="Optional output HTML file for the generated dashboard.")
	args = parser.parse_args()

	demo_mode = args.demo or not find_api_token()
	if demo_mode:
		if args.demo:
			print("🛑 Demo mode selected. Switching to OFFLINE DEMO MODE.")
		else:
			print("🛑 API token not found. Switching to OFFLINE DEMO MODE.")
		data_results = load_demo_data()
		country_code = DEMO_COUNTRY_CODE
		destination_zone = DEMO_DESTINATION_ZONE
		local_tz = lookup_area(country_code).tz
		period_start, period_end = get_data_period(data_results)
		print(
			f"Demo dataset: {country_code} to {destination_zone}, "
			f"{period_start:%Y-%m-%d %H:%M %Z} to {period_end:%Y-%m-%d %H:%M %Z}"
		)
	else:
		print("🌐 API token detected. Running in ONLINE MODE.")
		if args.country_code and args.destination_zone and args.start and args.end:
			country_code = args.country_code.upper()
			destination_zone = args.destination_zone.upper()
			local_tz = lookup_area(country_code).tz
			start_ts = pd.Timestamp(args.start)
			end_ts = pd.Timestamp(args.end)
			if start_ts.tzinfo is None:
				start_ts = start_ts.tz_localize(local_tz)
			if end_ts.tzinfo is None:
				end_ts = end_ts.tz_localize(local_tz)
			data_results = query_live_data(country_code, destination_zone, start_ts, end_ts)
		else:
			country_code, local_tz = prompt_for_origin_zone()
			destination_zone = prompt_for_destination_zone(country_code)
			start_ts, end_ts = prompt_for_date_range(local_tz)
			data_results = query_live_data(country_code, destination_zone, start_ts, end_ts)

	fig = build_dashboard(data_results, country_code, destination_zone, local_tz)

	if args.output:
		output_path = Path(args.output).expanduser()
		output_path.parent.mkdir(parents=True, exist_ok=True)
		fig.write_html(output_path, include_plotlyjs="cdn")
		print(f"✅ Dashboard saved to: {output_path}")
	else:
		fig.show()


if __name__ == "__main__":
	main()