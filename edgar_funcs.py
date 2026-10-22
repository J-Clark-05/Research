from io import StringIO
import requests
import random
import pandas as pd
import numpy as np
import json
import datetime as dt
import pandas_market_calendars as mcal
import datetime
import time

def add_zeros(string):
    return string.zfill(10)

def get_point_in_time_cik(
    headers: dict, 
    dates: list[str],
):
    st, qs = int(dates[0][:4]), int(dates[0][5:])
    ed, qe = int(dates[1][:4]), int(dates[1][5:])

    urls = [
    f"https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{quarter}/master.idx"
    for year in range(st, ed + 1)
    for quarter in range(1, 5)
    if (year, quarter) >= (st, qs)
    and (year, quarter) <= (ed, qe)
    ]

    cik_list = []
    relevant_forms = ['10-K', '10-Q', '20-F', '40-F']
    for url in urls:
        print(f"Requesting {url}...")
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        dfs = response.text
        header_line = "CIK|Company Name|Form Type|Date Filed|Filename"
        start = dfs.index(header_line)
        filings = pd.read_csv(
            StringIO(dfs[start:]),
            sep="|",
            skiprows=[1],
            dtype={"CIK": str}
        )
        relevant_coms = filings[filings['Form Type'].isin(relevant_forms)]
        new_ciks = relevant_coms['CIK'].unique()
        cik_list = list(set(cik_list) | set(new_ciks))
        time.sleep(0.2)

    return cik_list

    
