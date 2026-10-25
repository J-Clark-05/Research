# Packages
import os
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
import threading
from concurrent.futures import ThreadPoolExecutor

from edgar_funcs import EdgarFunctions
headers = {'User-Agent': os.environ['email']}
ef = EdgarFunctions(headers=headers)

pd.set_option('display.max_columns', None)
pd.set_option('display.width', None)

# Investor Calendar
nyse = mcal.get_calendar('NYSE')
cal = nyse.schedule(start_date='2008-01-01', end_date=datetime.datetime.now().date().strftime('%Y-%m-%d'))
trading_days = cal.index

# Portfolio Rebalance Dates - Quarterly - Last day of month in March, June, September, December
month_end = trading_days.to_series().groupby([trading_days.year, trading_days.month]).last()
rbal_dates = month_end[month_end.dt.month.isin([3, 6, 9, 12])]
data = pd.DataFrame(index=trading_days)
data['RebalDate'] = data.index.isin(rbal_dates)

# Factor Observation and Weighting Dates - Five trading days before the Portfolio Rebalance Dates
get_rbal_dates = data[data['RebalDate'] == True].index
five_days_before = data.index[data.index.get_indexer(get_rbal_dates) - 5]
data['ReferenceDate'] = data.index.isin(five_days_before)

# Point in time option available - Would require point in time stock price data to match
date_range = ['2008Q1', '2026Q3']
raw_cik_list = ef.get_point_in_time_cik(date_range)
most_recent_quarter = ef.get_point_in_time_cik(['2026Q4', '2026Q4'])
cik_list = [ef.add_zeros(cik) for cik in raw_cik_list]
additions = [ef.add_zeros(cik) for cik in most_recent_quarter]

# Currently Active CIKs
active_cik_raw = requests.get("https://www.sec.gov/files/company_tickers.json", headers=headers).json()
active_cik = [ef.add_zeros(str(d["cik_str"])) for d in active_cik_raw.values()]

# Are any active cik missing from cik list
miss = [cik for cik in active_cik if cik not in cik_list]
miss_active = [cik for cik in cik_list if cik not in active_cik]
print(f'There are {len(miss)} companies not in the master.idx files')
print(f'There are {len(miss_active)} companies not in the active list')

cik_list = cik_list + miss

# Get the SIC codes and country of operation for Firms in the List
path = 'InputData/International Programs - Two- Letter State Abbreviations.html'
states_table = pd.read_html(path) # Dataframe in a list
states_codes = states_table[0][1].tolist()

# Global rate limiter: at least 0.2 seconds between request starts
lock = threading.Lock()
next_request_time = [0.0]

with ThreadPoolExecutor(max_workers=5) as executor:
    results = list(executor.map(ef.get_company_info, cik_list))

sic_codes = [r["sic_codes"] for r in results]
sic_description = [r["sic_description"] for r in results]
state = [r["state"] for r in results]
state_description = [r["state_description"] for r in results]
company_name = [r["company_name"] for r in results]
ticker = [r["ticker"] for r in results]

id_frame = pd.DataFrame({
    "Company": company_name,
    "Ticker": ticker,
    "CIK": filtered_list,
    "State": state,
    "StateDescription": state_description,
    "SIC": sic_codes, 
    "SICDescription": sic_description
})

# Remove Firms Registered Outside of the U.S.
# id_frame['NotUSRegistered'] = id_frame['State'].apply(lambda x: x not in states_codes)
# id_frame = id_frame[id_frame['NotUSRegistered'] == False]

# Check the Desired Variables are Available for the Desired Firms
vars = pd.DataFrame({'Variable': []})
for cik in id_frame["CIK"].to_list():
    ticker = id_frame["Ticker"][id_frame["CIK"].to_list().index(cik)]
    raw_data = requests.get(
         f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json',
         headers=headers)
    extract = raw_data.json()['facts']['us-gaap'].keys()
    tempdf = pd.DataFrame({'Variable': extract, f'{ticker}': [1]*len(extract)})
    vars = pd.merge(vars, tempdf, how='outer', on='Variable').fillna(0)
vars.set_index('Variable', inplace=True)

query = {
        'Revenues': 'flow', 
        'NetCashProvidedByUsedInOperatingActivities': 'flow',
        'NetIncomeLoss': 'flow',
        'EarningsPerShareBasic': 'flow',
        'EarningsPerShareDiluted': 'flow',        
        # 'Depreciation': 'flow',
        'DepreciationAndAmortization': 'flow',
        # 'DepreciationDepletionAndAmortization': 'flow',
        'Assets': 'stock', 
        # 'AssetsCurrent': 'stock',
        # 'AssetsNoncurrent': 'stock',  
        'Liabilities': 'stock',
        'LiabilitiesCurrent': 'stock',
        # 'LiabilitiesNoncurrent': 'stock',
        # 'PropertyPlantAndEquipmentGross': 'stock',
        'PropertyPlantAndEquipmentNet': 'stock',
        # 'LongTermDebtNoncurrent': 'stock',
        # 'LongTermDebtCurrent': 'stock',
        'LongTermDebt': 'stock',
    }

# Fundamentals Lags
lags = 8
col_heads = [f'{item}L{lag}' for item in query.keys() for lag in range(0, lags + 1)]
for item in col_heads:
    data[item] = np.nan

# Get Data - Method 1
meta_data_dict = {}
data_dict = {}
for cik in id_frame["CIK"].to_list():
    ticker = id_frame["Ticker"][id_frame["CIK"].to_list().index(cik)]
    data_dict[ticker] = data.copy()
    data_dict[ticker]['Ticker'] = ticker
    raw_data = requests.get(
     f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json',
     headers=headers
    )
    for feature in query.keys():
        df = pd.concat([
            pd.DataFrame(item, index=range(1)) for item in 
            raw_data.json()['facts']['us-gaap'][feature]['units']['USD']
        ]).reset_index(drop=True)

        df['end'] = pd.to_datetime(df['end'])
        df['filed'] = pd.to_datetime(df['filed'])

        if query[feature] == 'flow':
            df['StartEndDiff'] = (df['end'] - pd.to_datetime(df['start'])).dt.days
            df = df[df['StartEndDiff'] < 120]

        filed_array = np.tile(df['filed'].to_numpy()[:, None], (1, len(five_days_before)))
        ref_array = np.tile(five_days_before.to_numpy(), (len(df), 1))
        obj = (
            ref_array.astype('datetime64[s]').astype('float') - 
            filed_array.astype('datetime64[s]').astype('float')
            )
        obj = np.where(obj < 0, 1e15, obj)
        points = np.argmin(obj, axis=1)
        rdates = ref_array[1, points]
        mapper = dict(zip(df['filed'], rdates))
        df['MappedReferenceDate'] = df['filed'].map(mapper)
        df['Lookback'] = np.round((df['MappedReferenceDate'] - df['end']).dt.days / 90, 0) - 1

        for group in df.groupby('end'): 
            sub_df = group[1].reset_index(drop=True)
            for row in sub_df.itertuples():
                date = row.MappedReferenceDate
                lag = row.Lookback
                if lag <= lags:
                    lab = str(int(lag))
                    data_dict[ticker].loc[date, f'{feature}L{lab}'] = row.val

        keepers = [f'{feature}L{lag}' for lag in range(0, lags + 1)]
        filtered_frame = data_dict[ticker][data_dict[ticker]['ReferenceDate'] == True][keepers]

        for row in filtered_frame.itertuples():
            date = row.Index
            prev_date = filtered_frame.index[filtered_frame.index.get_loc(date) - 1]
            if row.Index > filtered_frame.index[0]:
                current_row = filtered_frame.loc[date]
                prev_row = filtered_frame.loc[prev_date].shift(1)
                filtered_frame.loc[date] = np.where(
                    current_row.isnull(), prev_row, current_row
                )

        data_dict[ticker].loc[filtered_frame.index, keepers] = filtered_frame

        first_valid = data_dict[ticker][f'{feature}L0'].first_valid_index() 
        a = filtered_frame.loc[first_valid:].index
        b = {key: a[key] for key in range(lags)}
        c = {v: k for k, v in b.items()}
        d = {key: [col for col in keepers if float(col[-1]) > c[key]] for key in c.keys()}

        for date, cols in d.items():
            data_dict[ticker].loc[date, cols] = np.nan

        for col in keepers:
            data_dict[ticker][col] = data_dict[ticker][col].ffill()

        meta_data_dict[f'{ticker}_{feature}'] = df

fdata = pd.concat(data_dict.values(), axis=0)
fdata = fdata.set_index(['Ticker'], append=True).sort_index()

