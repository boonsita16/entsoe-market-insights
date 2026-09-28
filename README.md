# entsoe-market-insights

An interactive Plotly dashboard for analyzing system load, generation by merit-order grouping, net position, cross-border flows, and day-ahead electricity prices using ENTSO-E data.

## Project structure

- [notebook/01-data-to-csv.ipynb](notebook/01-data-to-csv.ipynb): downloads ENTSO-E demo data and saves it as CSV files.
- [notebook/02-market-plot.ipynb](notebook/02-market-plot.ipynb): the original exploratory notebook kept for reference. See the plot in [nbviewer](https://nbviewer.org/github/boonsita16/entsoe-market-insights/blob/main/notebook/02-market-plot.ipynb).
- [market_plot.py](market_plot.py): the reusable source code that creates the dashboard. This is the version intended for ongoing development and script-based execution.

## Run the dashboard

From the repository root, running without arguments starts the interactive workflow:

```bash
python market_plot.py
```

The script will then:

1. show the available origin bidding zones
2. prompt for the origin zone
3. show the valid destination zone options for that area
4. prompt for the destination zone
5. show the date format and ask for the start and end date
6. render the interactive Plotly dashboard

For a non-interactive run using the live API:

```bash
python market_plot.py --country-code DK_1 --destination-zone DE_LU --start 20260920 --end 20260925
```

For the bundled demo data:

```bash
python market_plot.py --demo
```

To save the output to an HTML file instead of opening a browser window:

```bash
python market_plot.py --demo --output dashboard.html
```

## Notes

- The notebook remains unchanged for documentation and exploratory work.
- The reusable Python module centralizes the data-loading and Plotly chart logic.
- Demo mode works with the bundled sample CSVs in the notebook folder and does not require an API token.
- Need to include gas price according to prices spike on 22 Sep 
