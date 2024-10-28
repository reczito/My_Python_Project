import ccxt
import os
import pandas as pd
import numpy as np
import time
import datetime
import retrying

print("Starting the script...")

# Initialize the ccxt exchange instance with error handling and retry mechanism
@retrying.retry(stop_max_attempt_number=5, wait_fixed=2000)
def initialize_exchange():
    try:
        return ccxt.mexc({
            'apiKey': os.getenv('MEXC_API_KEY'),
            'secret': os.getenv('MEXC_SECRET_KEY'),
            'enableRateLimit': True,
        })
    except Exception as e:
        print(f"Error initializing exchange: {e}")
        raise

try:
    exchange = initialize_exchange()
except Exception as e:
    print(f"Failed to initialize exchange after retries: {e}")
    exit(1)

# Set parameters
symbol = 'BTC/USDT'
timeframe = '15m'
lookback_period = 14
fibonacci_levels = [0.236, 0.382, 0.5, 0.618, 0.786]
rsi_overbought = 70
rsi_oversold = 30
risk_percentage = 0.01  # Risk 1% of the balance per trade
stop_loss_factor = 0.02  # Set stop loss at 2% below/above entry price
take_profit_factor = 0.06  # Set take profit at 6% below/above entry price for a 1:3 risk-to-reward ratio
leverage = 20
risk_per_trade = 20  # $20 per trade
risk_to_reward_ratio = 3  # 1:3 risk-to-reward ratio
stop_loss_pct = 1 / leverage  # 1% price move as stop loss
take_profit_pct = 3 / leverage  # 3% price move as take profit

# Cached balance to avoid frequent API calls
cached_balance = None
last_balance_fetch_time = None
balance_cache_duration = 300  # Cache balance for 300 seconds (5 minutes)

# Function to fetch OHLCV data
def fetch_data(symbol, timeframe):
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe, limit=100)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        print(f"Error fetching data: {e}")
        return None

# Function to identify significant range breaks by analyzing historical price data
def identify_range_breaks(data):
    data['high_max'] = data['high'].rolling(window=20).max()
    data['low_min'] = data['low'].rolling(window=20).min()
    if data['close'].iloc[-1] > data['high_max'].iloc[-2]:
        return 'breakout_up'
    elif data['close'].iloc[-1] < data['low_min'].iloc[-2]:
        return 'breakout_down'
    return None

# Function to calculate RSI
def calculate_rsi(data, period):
    delta = data['close'].diff(1)
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

# Function to calculate Fibonacci retracement levels and identify corrective waves
def calculate_fibonacci(data):
    max_price = data['high'].max()
    min_price = data['low'].min()
    fibonacci_levels_dict = {}
    for level in fibonacci_levels:
        fibonacci_levels_dict[level] = min_price + (max_price - min_price) * level
    
    # Identify corrective wave based on Fibonacci levels
    last_close = data['close'].iloc[-1]
    if min_price < last_close < max_price:
        for level, value in fibonacci_levels_dict.items():
            if abs(last_close - value) / max_price < 0.02:  # Consider a corrective wave if close to a Fibonacci level (2% threshold)
                print(f"Corrective wave identified near Fibonacci level {level}: {value}")
                break
    return fibonacci_levels_dict

# Function to calculate position size based on risk management
def calculate_position_size():
    global cached_balance, last_balance_fetch_time
    current_time = time.time()
    # Fetch balance if cache is expired or not available
    if cached_balance is None or (last_balance_fetch_time is None or (current_time - last_balance_fetch_time) > balance_cache_duration):
        try:
            cached_balance = exchange.fetch_balance()['total']['USDT']
            last_balance_fetch_time = current_time
        except Exception as e:
            print(f"Error fetching balance: {e}")
            return 0
    return risk_per_trade / cached_balance  # Calculate position size based on risk per trade

# Function to place an order with risk management (stop loss and take profit)
def place_order(order_type, amount, entry_price):
    try:
        if order_type == 'buy':
            stop_loss_price = entry_price * (1 - stop_loss_pct)
            take_profit_price = entry_price * (1 + take_profit_pct)
            order = exchange.create_limit_buy_order(symbol, amount, entry_price * 1.01, {'leverage': leverage})  # Limit order to mitigate slippage
            # Place stop loss and take profit orders
            exchange.create_order(symbol, 'limit', 'sell', amount, take_profit_price, {'stopPrice': stop_loss_price})
        elif order_type == 'sell':
            stop_loss_price = entry_price * (1 + stop_loss_pct)
            take_profit_price = entry_price * (1 - take_profit_pct)
            order = exchange.create_limit_sell_order(symbol, amount, entry_price * 0.99, {'leverage': leverage})  # Limit order to mitigate slippage
            # Place stop loss and take profit orders
            exchange.create_order(symbol, 'limit', 'buy', amount, take_profit_price, {'stopPrice': stop_loss_price})
        print(f"Order placed: {order}")
        print(f"Stop Loss set at: {stop_loss_price}, Take Profit set at: {take_profit_price}")
    except Exception as e:
        print(f"Error placing order: {e}")

# Function to check moving average cross and confirm new trend push
def moving_average_cross(data, short_window=50, long_window=200):
    data['ma_short'] = data['close'].rolling(window=short_window).mean()
    data['ma_long'] = data['close'].rolling(window=long_window).mean()
    # Confirm new trend push by ensuring the short MA crosses above/below the long MA and there is price momentum
    if data['ma_short'].iloc[-1] > data['ma_long'].iloc[-1] and data['ma_short'].iloc[-2] <= data['ma_long'].iloc[-2]:
        # Additional check for trend push: ensure recent price action supports upward movement
        if data['close'].iloc[-1] > data['ma_short'].iloc[-1] and data['close'].iloc[-1] > data['high'].rolling(window=5).max().iloc[-2]:
            return 'bullish'
    elif data['ma_short'].iloc[-1] < data['ma_long'].iloc[-1] and data['ma_short'].iloc[-2] >= data['ma_long'].iloc[-2]:
        # Additional check for trend push: ensure recent price action supports downward movement
        if data['close'].iloc[-1] < data['ma_short'].iloc[-1] and data['close'].iloc[-1] < data['low'].rolling(window=5).min().iloc[-2]:
            return 'bearish'
    return None

# Function to check for impulse waves following corrective waves
def identify_impulse_wave(data, fibonacci_levels_dict):
    max_price = data['high'].max()
    min_price = data['low'].min()
    last_close = data['close'].iloc[-1]
    # An impulse wave is identified if price moves significantly beyond key Fibonacci levels after a corrective wave
    for level, value in fibonacci_levels_dict.items():
        if last_close > value and level >= 0.618:  # Check for strong continuation beyond significant Fibonacci levels
            print(f"Impulse wave identified following corrective wave at level {level}: {value}")
            return 'impulse_up'
        elif last_close < value and level <= 0.382:  # Check for strong decline beyond significant Fibonacci levels
            print(f"Impulse wave identified following corrective wave at level {level}: {value}")
            return 'impulse_down'
    return None

# Main trading logic
def trading_bot():
    last_run_time = None
    while True:
        current_time = datetime.datetime.utcnow()
        if last_run_time is None or (current_time - last_run_time).seconds >= 60:
            market_volatility = abs(exchange.fetch_ticker(symbol)['high'] - exchange.fetch_ticker(symbol)['low']) / exchange.fetch_ticker(symbol)['low']
            interval = max(10, int(60 * market_volatility))  # Adjust interval based on market volatility, with a minimum of 10 seconds
            last_run_time = current_time
            time.sleep(interval)
            # Fetching the data
            data = fetch_data(symbol, timeframe)
            if data is None:
                continue

            # Calculate indicators
            data['rsi'] = calculate_rsi(data, lookback_period)
            fibonacci_levels_dict = calculate_fibonacci(data)
            ma_cross = moving_average_cross(data)
            range_break = identify_range_breaks(data)
            impulse_wave = identify_impulse_wave(data, fibonacci_levels_dict)

            current_price = data['close'].iloc[-1]
            current_rsi = data['rsi'].iloc[-1]

            print(f"Current Price: {current_price}, RSI: {current_rsi}")
            print(f"Fibonacci Levels: {fibonacci_levels_dict}")

            # Trading logic based on RSI, Fibonacci, moving average cross, range breaks, and impulse waves
            amount = calculate_position_size() / current_price
            
            # Ensure the calculated amount is within acceptable limits
            min_amount = 0.0001  # Example minimum amount, adjust based on platform requirements
            max_amount = 10  # Example maximum amount, adjust based on platform requirements
            if amount < min_amount:
                print(f"Calculated amount ({amount}) is below the minimum allowed. Skipping trade.")
                continue
            elif amount > max_amount:
                print(f"Calculated amount ({amount}) exceeds the maximum allowed. Adjusting to maximum.")
                amount = max_amount

            if (range_break == 'breakout_up' or ma_cross == 'bullish') and impulse_wave == 'impulse_up' and current_rsi < rsi_oversold:
                print("Bullish breakout or crossover detected with RSI oversold condition and confirmed impulse wave. Placing Buy order.")
                place_order('buy', amount, current_price)
            elif (range_break == 'breakout_down' or ma_cross == 'bearish') and impulse_wave == 'impulse_down' and current_rsi > rsi_overbought:
                print("Bearish breakout or crossover detected with RSI overbought condition and confirmed impulse wave. Placing Sell order.")
                place_order('sell', amount, current_price)

            # Implement trailing stop loss for better gains (optional)
            # Placeholder for trailing stop logic - to be implemented as needed

if __name__ == "_main_":
    trading_bot()
