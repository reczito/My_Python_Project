import ccxt
import os
import pandas as pd
import numpy as np
import time
import datetime
import retrying
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Initialize the ccxt exchange instance with error handling and retry mechanism
@retrying.retry(stop_max_attempt_number=5, wait_fixed=2000)
def initialize_exchange():
    try:
        logging.info("Attempting to initialize exchange...")
        return ccxt.mexc({
            'apiKey': os.getenv('MEXC_API_KEY'),
            'secret': os.getenv('MEXC_SECRET_KEY'),
            'enableRateLimit': True,
        })
    except Exception as e:
        logging.error(f"Error initializing exchange: {e}")
        raise

try:
    exchange = initialize_exchange()
    logging.info("Exchange initialized successfully.")
except Exception as e:
    logging.error(f"Failed to initialize exchange after retries: {e}")
    exit(1)

# Set parameters
symbol = 'BTC/USDT'
timeframe = '15m'
lookback_period = 14
fibonacci_levels = [0.236, 0.382, 0.5, 0.618, 0.786]
rsi_overbought = 70
rsi_oversold = 30
risk_percentage = 0.01
stop_loss_factor = 0.02
take_profit_factor = 0.06
leverage = 20
risk_per_trade = 20
risk_to_reward_ratio = 3
stop_loss_pct = 1 / leverage
take_profit_pct = 3 / leverage

# Cached balance to avoid frequent API calls
cached_balance = None
last_balance_fetch_time = None
balance_cache_duration = 300

# Function to fetch OHLCV data
def fetch_data(symbol, timeframe):
    try:
        logging.info(f"Fetching data for {symbol} at {timeframe} timeframe...")
        bars = exchange.fetch_ohlcv(symbol, timeframe, limit=100)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        logging.info("Data fetched successfully.")
        return df
    except Exception as e:
        logging.error(f"Error fetching data: {e}")
        return None

# Function to identify significant range breaks
def identify_range_breaks(data):
    data['high_max'] = data['high'].rolling(window=20).max()
    data['low_min'] = data['low'].rolling(window=20).min()
    if data['close'].iloc[-1] > data['high_max'].iloc[-2]:
        logging.info("Detected breakout up.")
        return 'breakout_up'
    elif data['close'].iloc[-1] < data['low_min'].iloc[-2]:
        logging.info("Detected breakout down.")
        return 'breakout_down'
    return None

# Function to calculate RSI
def calculate_rsi(data, period):
    logging.info("Calculating RSI...")
    delta = data['close'].diff(1)
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    logging.info("RSI calculated.")
    return rsi

# Function to calculate Fibonacci retracement levels
def calculate_fibonacci(data):
    logging.info("Calculating Fibonacci levels...")
    max_price = data['high'].max()
    min_price = data['low'].min()
    fibonacci_levels_dict = {}
    for level in fibonacci_levels:
        fibonacci_levels_dict[level] = min_price + (max_price - min_price) * level
    logging.info(f"Fibonacci levels: {fibonacci_levels_dict}")
    return fibonacci_levels_dict

# Function to calculate position size based on risk management
def calculate_position_size():
    global cached_balance, last_balance_fetch_time
    current_time = time.time()
    if cached_balance is None or (last_balance_fetch_time is None or (current_time - last_balance_fetch_time) > balance_cache_duration):
        try:
            cached_balance = exchange.fetch_balance()['total']['USDT']
            last_balance_fetch_time = current_time
            logging.info(f"Fetched balance: {cached_balance}")
        except Exception as e:
            logging.error(f"Error fetching balance: {e}")
            return 0
    return risk_per_trade / cached_balance

# Function to place an order with risk management
def place_order(order_type, amount, entry_price):
    try:
        logging.info(f"Placing {order_type} order with amount {amount} at price {entry_price}...")
        if order_type == 'buy':
            stop_loss_price = entry_price * (1 - stop_loss_pct)
            take_profit_price = entry_price * (1 + take_profit_pct)
            order = exchange.create_limit_buy_order(symbol, amount, entry_price * 1.01, {'leverage': leverage})
            exchange.create_order(symbol, 'limit', 'sell', amount, take_profit_price, {'stopPrice': stop_loss_price})
        elif order_type == 'sell':
            stop_loss_price = entry_price * (1 + stop_loss_pct)
            take_profit_price = entry_price * (1 - take_profit_pct)
            order = exchange.create_limit_sell_order(symbol, amount, entry_price * 0.99, {'leverage': leverage})
            exchange.create_order(symbol, 'limit', 'buy', amount, take_profit_price, {'stopPrice': stop_loss_price})
        logging.info(f"Order placed: {order}")
    except Exception as e:
        logging.error(f"Error placing order: {e}")

# Main trading logic
def trading_bot():
    last_run_time = None
    while True:
        current_time = datetime.datetime.utcnow()
        if last_run_time is None or (current_time - last_run_time).seconds >= 60:
            logging.info("Starting new trading cycle...")
            last_run_time = current_time
            data = fetch_data(symbol, timeframe)
            if data is None:
                logging.warning("No data fetched. Skipping this cycle.")
                continue

            data['rsi'] = calculate_rsi(data, lookback_period)
            fibonacci_levels_dict = calculate_fibonacci(data)
            ma_cross = moving_average_cross(data)
            range_break = identify_range_breaks(data)
            impulse_wave = identify_impulse_wave(data, fibonacci_levels_dict)

            current_price = data['close'].iloc[-1]
            current_rsi = data['rsi'].iloc[-1]
            amount = calculate_position_size() / current_price
            min_amount, max_amount = 0.0001, 10

            if (range_break == 'breakout_up' or ma_cross == 'bullish') and impulse_wave == 'impulse_up' and current_rsi < rsi_oversold:
                logging.info("Bullish conditions met - placing Buy order.")
                place_order('buy', amount, current_price)
            elif (range_break == 'breakout_down' or ma_cross == 'bearish') and impulse_wave == 'impulse_down' and current_rsi > rsi_overbought:
                logging.info("Bearish conditions met - placing Sell order.")
                place_order('sell', amount, current_price)

if __name__ == "__main__":
    trading_bot()
