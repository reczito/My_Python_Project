import ccxt
import os
import pandas as pd
import numpy as np
import time
import datetime
import retrying
import logging
import streamlit as st

# Streamlit layout configuration
st.set_page_config(page_title="TradeBot Dashboard", layout="wide")
st.title("TradeBot Monitoring Dashboard")

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Initialize the exchange instance
@retrying.retry(stop_max_attempt_number=5, wait_fixed=2000)
def initialize_exchange():
    try:
        return ccxt.mexc({
            'apiKey': os.getenv('MEXC_API_KEY'),
            'secret': os.getenv('MEXC_SECRET_KEY'),
            'enableRateLimit': True,
        })
    except Exception as e:
        logging.error(f"Error initializing exchange: {e}")
        raise

# Try initializing the exchange
try:
    exchange = initialize_exchange()
    st.success("Exchange initialized successfully.")
except Exception as e:
    st.error(f"Failed to initialize exchange: {e}")
    exit(1)

# Trading parameters
symbol = 'BTC/USDT'
timeframe = '15m'
lookback_period = 14
fibonacci_levels = [0.236, 0.382, 0.5, 0.618, 0.786]
rsi_overbought = 70
rsi_oversold = 30

# Function to fetch OHLCV data
def fetch_data(symbol, timeframe):
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe, limit=100)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        logging.error(f"Error fetching data: {e}")
        st.warning("Error fetching data.")
        return None

# Function to calculate RSI
def calculate_rsi(data, period):
    delta = data['close'].diff(1)
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

# Function to calculate Fibonacci levels
def calculate_fibonacci(data):
    max_price = data['high'].max()
    min_price = data['low'].min()
    return {level: min_price + (max_price - min_price) * level for level in fibonacci_levels}

# Main Streamlit trading bot display
def trading_dashboard():
    last_run_time = None
    while True:
        current_time = datetime.datetime.utcnow()
        if last_run_time is None or (current_time - last_run_time).seconds >= 60:
            last_run_time = current_time

            # Fetch and display data
            data = fetch_data(symbol, timeframe)
            if data is None:
                st.warning("No data fetched. Skipping this cycle.")
                continue

            # RSI calculation and display
            data['rsi'] = calculate_rsi(data, lookback_period)
            current_rsi = data['rsi'].iloc[-1]
            st.metric("Current RSI", f"{current_rsi:.2f}")

            # Display Fibonacci levels
            fibonacci_levels_dict = calculate_fibonacci(data)
            st.subheader("Fibonacci Levels")
            for level, value in fibonacci_levels_dict.items():
                st.write(f"Level {level}: {value:.2f}")

            # Display last 5 rows of fetched data for inspection
            st.subheader("Recent Data")
            st.dataframe(data.tail(5))

            # Trading signal check
            trade_signal = "None"
            if current_rsi < rsi_oversold:
                trade_signal = "Buy signal (Oversold)"
            elif current_rsi > rsi_overbought:
                trade_signal = "Sell signal (Overbought)"
            st.write(f"Trade Signal: {trade_signal}")

            # Streamlit refresh interval
            time.sleep(5)

# Run the Streamlit application
if __name__ == "__main__":
    trading_dashboard()