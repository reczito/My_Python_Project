import ccxt
import pandas as pd
import time
import datetime
import logging
import numpy as np
from retrying import retry

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Initialize exchange with error handling
@retry(stop_max_attempt_number=5, wait_fixed=2000)
def initialize_exchange():
    logging.info("Initializing exchange...")
    try:
        return ccxt.mexc({
            'apiKey': 'mx0vglH2fyXWUlKoSj',  # Replace with your API key
            'secret': '51885a5fa21e424e9363cf19606986dc',  # Replace with your Secret key
        })
    except Exception as e:
        logging.error(f"Exchange initialization failed: {e}")
        raise

exchange = initialize_exchange()
symbol = 'BTC/USDT'
timeframe = '15m'
lookback_period = 14
rsi_overbought, rsi_oversold = 70, 30
leverage, balance_cache_duration = 20, 300
stop_loss_pct, take_profit_pct = 1 / leverage, 3 / leverage

# Caching balance
cached_balance, last_balance_fetch_time = None, None

def fetch_data(symbol, timeframe):
    logging.info(f"Fetching {symbol} data for {timeframe} timeframe...")
    try:
        # Increase the limit to fetch up to 200 data points, if supported by the exchange
        bars = exchange.fetch_ohlcv(symbol, timeframe, limit=200)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        logging.error(f"Error fetching data: {e}")

def calculate_rsi(data, period=14):
    delta = data['close'].diff(1)
    gain, loss = delta.clip(lower=0), -delta.clip(upper=0)
    avg_gain, avg_loss = gain.rolling(window=period).mean(), loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    data['rsi'] = 100 - (100 / (1 + rs))
    logging.info(f"RSI calculated: {data['rsi'].iloc[-1]:.2f}")
    return data

def moving_average_cross(data, short_window=50, long_window=200):
    # Calculate moving averages
    data['ma_short'] = data['close'].rolling(window=short_window).mean()
    data['ma_long'] = data['close'].rolling(window=long_window).mean()
    
    # Check if MA Long contains NaN values, indicating insufficient data points
    if data['ma_long'].isna().all():
        logging.warning("Insufficient data for MA Long calculation. Waiting for more data points.")
        return None

    logging.info(f"MA Short: {data['ma_short'].iloc[-1]}, MA Long: {data['ma_long'].iloc[-1]}")  # Log moving averages
    
    # Check for cross-over conditions if enough data is present
    if data['ma_short'].iloc[-1] > data['ma_long'].iloc[-1] and data['ma_short'].iloc[-2] <= data['ma_long'].iloc[-2]:
        return 'bullish'
    elif data['ma_short'].iloc[-1] < data['ma_long'].iloc[-1] and data['ma_short'].iloc[-2] >= data['ma_long'].iloc[-2]:
        return 'bearish'
    return None

def identify_range_break(data):
    high_max = data['high'].rolling(window=20).max()
    low_min = data['low'].rolling(window=20).min()
    logging.info(f"High Max: {high_max.iloc[-2]}, Low Min: {low_min.iloc[-2]}, Close: {data['close'].iloc[-1]}")  # Log range break values
    
    if data['close'].iloc[-1] > high_max.iloc[-2]:
        return 'breakout_up'
    elif data['close'].iloc[-1] < low_min.iloc[-2]:
        return 'breakout_down'
    return None

def calculate_position_size():
    global cached_balance, last_balance_fetch_time
    current_time = time.time()
    if cached_balance is None or (last_balance_fetch_time is None or (current_time - last_balance_fetch_time) > balance_cache_duration):
        try:
            cached_balance = exchange.fetch_balance()['total']['USDT']
            last_balance_fetch_time = current_time
            logging.info(f"Current balance: {cached_balance}")
        except Exception as e:
            logging.error(f"Error fetching balance: {e}")
            return 0
    return 0.01 * cached_balance

def place_order(order_type, amount, entry_price):
    try:
        logging.info(f"Attempting to place {order_type} order with amount {amount} at {entry_price}...")
        if order_type == 'buy':
            stop_loss_price = entry_price * (1 - stop_loss_pct)
            take_profit_price = entry_price * (1 + take_profit_pct)
            order = exchange.create_limit_buy_order(symbol, amount, entry_price, {'leverage': leverage})
            exchange.create_order(symbol, 'limit', 'sell', amount, take_profit_price, {'stopPrice': stop_loss_price})
            logging.info(f"Buy order placed successfully with Stop Loss at {stop_loss_price} and Take Profit at {take_profit_price}")
        elif order_type == 'sell':
            stop_loss_price = entry_price * (1 + stop_loss_pct)
            take_profit_price = entry_price * (1 - take_profit_pct)
            order = exchange.create_limit_sell_order(symbol, amount, entry_price, {'leverage': leverage})
            exchange.create_order(symbol, 'limit', 'buy', amount, take_profit_price, {'stopPrice': stop_loss_price})
            logging.info(f"Sell order placed successfully with Stop Loss at {stop_loss_price} and Take Profit at {take_profit_price}")
    except Exception as e:
        logging.error(f"{order_type.capitalize()} order placement failed: {e}")

def trading_bot():
    last_run_time = None
    while True:
        current_time = datetime.datetime.utcnow()
        if last_run_time is None or (current_time - last_run_time).seconds >= 60:
            last_run_time = current_time
            data = fetch_data(symbol, timeframe)
            if data is None:
                logging.warning("No data fetched. Skipping this cycle.")
                continue

            data = calculate_rsi(data, lookback_period)
            ma_cross = moving_average_cross(data)
            range_break = identify_range_break(data)
            current_price = data['close'].iloc[-1]
            current_rsi = data['rsi'].iloc[-1]
            amount = calculate_position_size() / current_price

            # Evaluate conditions for placing buy or sell orders
            if (range_break == 'breakout_up' or ma_cross == 'bullish') and current_rsi < rsi_oversold:
                logging.info(f"Buy conditions met (RSI: {current_rsi}, MA Cross: {ma_cross}, Range Break: {range_break})")
                place_order('buy', amount, current_price)
            elif (range_break == 'breakout_down' or ma_cross == 'bearish') and current_rsi > rsi_overbought:
                logging.info(f"Sell conditions met (RSI: {current_rsi}, MA Cross: {ma_cross}, Range Break: {range_break})")
                place_order('sell', amount, current_price)
            else:
                logging.info(f"No trade action taken. Conditions not met (RSI: {current_rsi}, MA Cross: {ma_cross}, Range Break: {range_break}).")

if __name__ == "__main__":
    trading_bot()
