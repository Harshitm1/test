import websocket
import json
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
import time
import threading
import os
import logging
from pathlib import Path

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trading.log'),
        logging.StreamHandler()
    ]
)

# Delta Exchange API configuration
WEBSOCKET_URL = "wss://socket.india.delta.exchange"  # Delta Exchange India WebSocket URL
API_KEY = "LEL40PL23vut3KwbAt7ISV60lAEx9Y"
API_SECRET = "Jfb0Utw2tFjXCJMW3zaQlXl4OniB5KhVyvf8XupreUsmq3PHEtPLqrF903hk"
SYMBOL = "ETHUSD"  # Trading pair
TIMEFRAME = "15m"   # Changed from 1m to 15m timeframe
MAX_RETRIES = 5     # Maximum number of connection retries
RETRY_DELAY = 5     # Delay between retries in seconds

class OrderBlocks:
    def __init__(self, symbol="ETHUSD", timeframe="15m", initial_capital=100, sensitivity=0.01):
        self.symbol = symbol
        self.timeframe = timeframe
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.sensitivity = sensitivity
        self.data = []
        self.bear_boxes = []
        self.bull_boxes = []
        self.trades = []
        self.pnl = []
        self.ws = None
        self.plot_counter = 0
        self.start_time = datetime.now()
        self.running = True
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.total_pnl = 0
        self.current_equity = initial_capital
        self.position = 0  # 0: no position, 1: long, -1: short
        self.entry_price = 0
        
        # Create trading_data directory if it doesn't exist
        Path("trading_data").mkdir(exist_ok=True)
        
        # Load existing data if available
        self.load_data()
        
    def load_data(self):
        try:
            data_file = Path("trading_data") / f"{self.symbol}_data.csv"
            if data_file.exists():
                df = pd.read_csv(data_file)
                df['timestamp'] = pd.to_datetime(df['timestamp'])
                self.data = df.to_dict('records')
                logging.info(f"Loaded {len(self.data)} historical candles")
        except Exception as e:
            logging.error(f"Error loading data: {e}")

    def save_data(self):
        try:
            if self.data:
                df = pd.DataFrame(self.data)
                df.to_csv(Path("trading_data") / f"{self.symbol}_data.csv", index=False)
                logging.info("Data saved successfully")
        except Exception as e:
            logging.error(f"Error saving data: {e}")
        
    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            if 'type' in data and data['type'] == 'candlestick_1m':
                candle = {
                    'timestamp': datetime.fromtimestamp(data['candle_start_time'] / 1000000),
                    'open': float(data['open']),
                    'high': float(data['high']),
                    'low': float(data['low']),
                    'close': float(data['close']),
                    'volume': float(data['volume'])
                }
                
                logging.info(f"\nNew {self.timeframe} Candle for {self.symbol}")
                logging.info(f"Time: {candle['timestamp']}")
                logging.info(f"Open: {candle['open']:.2f}")
                logging.info(f"High: {candle['high']:.2f}")
                logging.info(f"Low: {candle['low']:.2f}")
                logging.info(f"Close: {candle['close']:.2f}")
                logging.info(f"Volume: {candle['volume']:.2f}")
                
                self.data.append(candle)
                self.process_data()
                
                # Save data periodically
                if len(self.data) % 10 == 0:
                    self.save_data()
                
        except Exception as e:
            logging.error(f"Error processing message: {e}")

    def on_error(self, ws, error):
        logging.error(f"WebSocket error: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        logging.info("WebSocket connection closed")
        if self.running:
            logging.info("Attempting to reconnect...")
            time.sleep(5)
            self.connect()

    def on_open(self, ws):
        logging.info("WebSocket connection established")
        subscribe_message = {
            "type": "subscribe",
            "payload": {
                "channels": [
                    {
                        "name": f"candlestick_{self.timeframe}",
                        "symbols": [self.symbol]
                    }
                ]
            }
        }
        ws.send(json.dumps(subscribe_message))
        logging.info(f"Subscribed to {self.symbol} {self.timeframe} candlestick data")

    def calculate_pc(self):
        """Calculate percentage change over last 4 candles"""
        if len(self.data) < 5:
            return
            
        current_open = self.data[-1]['open']
        prev_4_open = self.data[-5]['open']
        pc = (current_open - prev_4_open) / prev_4_open * 100
        return pc

    def find_order_blocks(self):
        """Identify order blocks in the price action"""
        if len(self.data) < 16:  # Need at least 16 candles for lookback
            return
            
        current_idx = len(self.data) - 1
        current_pc = self.calculate_pc()
        if current_pc is None:
            return
            
        prev_pc = (self.data[-2]['open'] - self.data[-6]['open']) / self.data[-6]['open'] * 100
        
        # Check for recent boxes (within last 5 candles)
        recent_bear = any(current_idx - b['start_idx'] <= 5 for b in self.bear_boxes)
        recent_bull = any(current_idx - b['start_idx'] <= 5 for b in self.bull_boxes)
        
        # Bearish Order Block
        if prev_pc > -self.sensitivity and current_pc <= -self.sensitivity and not recent_bear:
            for i in range(current_idx-4, max(current_idx-16, -1), -1):
                if self.data[i]['close'] > self.data[i]['open']:  # Bullish candle
                    self.bear_boxes.append({
                        'start_idx': i,
                        'top': self.data[i]['high'],
                        'bot': self.data[i]['low'],
                        'time': self.data[i]['timestamp']
                    })
                    logging.info(f"\n=== BEARISH ORDER BLOCK DETECTED ===")
                    logging.info(f"Time: {self.data[i]['timestamp']}")
                    logging.info(f"Top: ${self.data[i]['high']:.2f}")
                    logging.info(f"Bottom: ${self.data[i]['low']:.2f}")
                    logging.info(f"Percentage Change: {current_pc:.2f}%")
                    logging.info("="*30 + "\n")
                    break
                    
        # Bullish Order Block
        if prev_pc < self.sensitivity and current_pc >= self.sensitivity and not recent_bull:
            for i in range(current_idx-4, max(current_idx-16, -1), -1):
                if self.data[i]['close'] < self.data[i]['open']:  # Bearish candle
                    self.bull_boxes.append({
                        'start_idx': i,
                        'top': self.data[i]['high'],
                        'bot': self.data[i]['low'],
                        'time': self.data[i]['timestamp']
                    })
                    logging.info(f"\n=== BULLISH ORDER BLOCK DETECTED ===")
                    logging.info(f"Time: {self.data[i]['timestamp']}")
                    logging.info(f"Top: ${self.data[i]['high']:.2f}")
                    logging.info(f"Bottom: ${self.data[i]['low']:.2f}")
                    logging.info(f"Percentage Change: {current_pc:.2f}%")
                    logging.info("="*30 + "\n")
                    break

    def should_enter_trade(self, current_price, current_time):
        """Determine if we should enter a trade"""
        if self.position != 0:  # Already in a position
            return False, None
            
        # Check for long entry (price near bullish order block)
        for box in self.bull_boxes:
            if (current_time - box['time']).total_seconds() <= 24*3600:  # Within 24 hours
                if abs(current_price - box['bot']) / box['bot'] < 0.005:  # Within 0.5% of bottom
                    return True, 'long'
                    
        # Check for short entry (price near bearish order block)
        for box in self.bear_boxes:
            if (current_time - box['time']).total_seconds() <= 24*3600:  # Within 24 hours
                if abs(current_price - box['top']) / box['top'] < 0.005:  # Within 0.5% of top
                    return True, 'short'
                    
        return False, None

    def should_exit_trade(self, current_price, current_time):
        """Determine if we should exit the trade"""
        if self.position == 0:  # No position to exit
            return False, 0
            
        # Calculate stop loss and take profit levels
        if self.position == 1:  # Long position
            stop_loss = self.entry_price * 0.99  # 1% below entry
            take_profit = self.entry_price * 1.02  # 2% above entry
            
            # Check stop loss
            if current_price <= stop_loss:
                pnl = (current_price - self.entry_price) / self.entry_price * 100
                return True, pnl
                
            # Check take profit
            if current_price >= take_profit:
                pnl = (current_price - self.entry_price) / self.entry_price * 100
                return True, pnl
                
            # Check for opposite signal (bearish order block)
            for box in self.bear_boxes:
                if (current_time - box['time']).total_seconds() <= 24*3600:
                    if abs(current_price - box['top']) / box['top'] < 0.005:
                        pnl = (current_price - self.entry_price) / self.entry_price * 100
                        return True, pnl
                        
        else:  # Short position
            stop_loss = self.entry_price * 1.01  # 1% above entry
            take_profit = self.entry_price * 0.98  # 2% below entry
            
            # Check stop loss
            if current_price >= stop_loss:
                pnl = (self.entry_price - current_price) / self.entry_price * 100
                return True, pnl
                
            # Check take profit
            if current_price <= take_profit:
                pnl = (self.entry_price - current_price) / self.entry_price * 100
                return True, pnl
                
            # Check for opposite signal (bullish order block)
            for box in self.bull_boxes:
                if (current_time - box['time']).total_seconds() <= 24*3600:
                    if abs(current_price - box['bot']) / box['bot'] < 0.005:
                        pnl = (self.entry_price - current_price) / self.entry_price * 100
                        return True, pnl
                        
        return False, 0

    def process_data(self):
        """Process new data and update trading decisions"""
        if len(self.data) < 16:
            return
            
        current_price = self.data[-1]['close']
        current_time = self.data[-1]['timestamp']
        
        # Find new order blocks
        self.find_order_blocks()
        
        # Check for trade entry
        if self.position == 0:
            should_enter, trade_type = self.should_enter_trade(current_price, current_time)
            if should_enter:
                self.position = 1 if trade_type == 'long' else -1
                self.entry_price = current_price
                self.trades.append({
                    'type': trade_type,
                    'entry_price': current_price,
                    'entry_time': current_time,
                    'entry_capital': self.current_capital
                })
                logging.info(f"\n=== ENTERING {trade_type.upper()} TRADE ===")
                logging.info(f"Entry Price: ${current_price:.2f}")
                logging.info(f"Current Capital: ${self.current_capital:.2f}")
                logging.info("="*30 + "\n")
                
        # Check for trade exit
        else:
            should_exit, pnl = self.should_exit_trade(current_price, current_time)
            if should_exit:
                self.trades[-1].update({
                    'exit_price': current_price,
                    'exit_time': current_time,
                    'pnl': pnl,
                    'exit_capital': self.current_capital * (1 + pnl/100)
                })
                self.current_capital *= (1 + pnl/100)
                self.total_pnl += pnl
                self.total_trades += 1
                if pnl > 0:
                    self.winning_trades += 1
                else:
                    self.losing_trades += 1
                    
                logging.info(f"\n=== EXITING {self.trades[-1]['type'].upper()} TRADE ===")
                logging.info(f"Exit Price: ${current_price:.2f}")
                logging.info(f"PnL: {pnl:.2f}%")
                logging.info(f"New Capital: ${self.current_capital:.2f}")
                logging.info("="*30 + "\n")
                
                self.position = 0
                self.entry_price = 0
                
        # Update equity curve
        self.pnl.append({
            'timestamp': current_time,
            'equity': self.current_capital if self.position == 0 else 
                     self.current_capital * (1 + (current_price - self.entry_price) / self.entry_price)
        })
        
        # Plot every 10 candles
        if len(self.data) % 10 == 0:
            self.plot_data()

    def plot_data(self):
        if len(self.data) < 2:
            return
            
        df = pd.DataFrame(self.data)
        
        # Create figure with 3 subplots
        fig = plt.figure(figsize=(15, 15))
        gs = fig.add_gridspec(3, 1, height_ratios=[3, 1, 1])
        ax1 = fig.add_subplot(gs[0])
        ax2 = fig.add_subplot(gs[1])
        ax3 = fig.add_subplot(gs[2])
        
        # Plot candlesticks
        for i in range(len(df)):
            if df['close'].iloc[i] >= df['open'].iloc[i]:
                color = 'green'
            else:
                color = 'red'
                
            ax1.plot([i, i], [df['low'].iloc[i], df['high'].iloc[i]], color=color, linewidth=1)
            ax1.plot([i, i], [df['open'].iloc[i], df['close'].iloc[i]], color=color, linewidth=3)
        
        # Plot order blocks and trade levels
        for ob in self.bear_boxes[-5:]:  # Show last 5 bearish order blocks
            ax1.axhspan(ob['bot'], ob['top'], xmin=0, xmax=1, color='red', alpha=0.2)
        
        for ob in self.bull_boxes[-5:]:  # Show last 5 bullish order blocks
            ax1.axhspan(ob['bot'], ob['top'], xmin=0, xmax=1, color='green', alpha=0.2)
        
        # Plot active trades
        for trade in self.trades:
            if trade['type'] == 'long':
                ax1.axhline(y=trade['entry_price'], color='g', linestyle='--', alpha=0.5)
            else:
                ax1.axhline(y=trade['entry_price'], color='r', linestyle='--', alpha=0.5)
        
        # Plot volume
        ax2.bar(range(len(df)), df['volume'], color=['green' if c >= o else 'red' for c, o in zip(df['close'], df['open'])])
        
        # Calculate and plot equity curve
        equity = [self.initial_capital]
        timestamps = [df['timestamp'].iloc[0]]
        
        for i in range(1, len(df)):
            total_pnl_percent = 0
            for trade in self.trades:
                if trade['entry_time'] <= df['timestamp'].iloc[i]:
                    if not trade['exit_time'] or trade['exit_time'] > df['timestamp'].iloc[i]:
                        if trade['type'] == 'long':
                            pnl = (df['close'].iloc[i] - trade['entry_price']) / trade['entry_price'] * 100
                        else:
                            pnl = (trade['entry_price'] - df['close'].iloc[i]) / trade['entry_price'] * 100
                        total_pnl_percent += pnl
            
            current_equity = self.initial_capital * (1 + total_pnl_percent/100)
            equity.append(current_equity)
            timestamps.append(df['timestamp'].iloc[i])
        
        ax3.plot(range(len(equity)), equity, 'b-', label='Equity')
        ax3.axhline(y=self.initial_capital, color='r', linestyle='--', label='Initial Capital')
        
        # Add trade markers to equity curve
        for trade in self.trades:
            if trade['exit_time']:
                idx = timestamps.index(trade['exit_time'])
                if trade['type'] == 'long':
                    color = 'g' if trade['pnl'] > 0 else 'r'
                else:
                    color = 'g' if trade['pnl'] > 0 else 'r'
                ax3.scatter(idx, equity[idx], color=color, marker='o', s=100)
        
        # Customize plots
        ax1.set_title(f'{self.symbol} {self.timeframe} Chart with Order Blocks and Trades')
        ax1.set_ylabel('Price')
        ax2.set_ylabel('Volume')
        ax2.set_xlabel('Candle')
        ax3.set_ylabel('Equity ($)')
        ax3.legend()
        
        # Save plot
        self.plot_counter += 1
        plt.savefig(f'trading_data/chart_{self.plot_counter}.png')
        plt.close()
        
        logging.info(f"\nChart saved as trading_data/chart_{self.plot_counter}.png")
        
        # Print trade statistics
        closed_trades = [t for t in self.trades if t['exit_time']]
        if closed_trades:
            total_pnl = sum(t['pnl'] for t in closed_trades)
            win_rate = len([t for t in closed_trades if t['pnl'] > 0]) / len(closed_trades) * 100
            
            logging.info("\n=== TRADE STATISTICS ===")
            logging.info(f"Total Trades: {len(closed_trades)}")
            logging.info(f"Win Rate: {win_rate:.2f}%")
            logging.info(f"Total PnL: {total_pnl:.2f}%")
            logging.info(f"Current Equity: ${equity[-1]:.2f}")
            logging.info("="*30 + "\n")

    def connect(self):
        websocket.enableTrace(True)
        self.ws = websocket.WebSocketApp(
            "wss://socket.india.delta.exchange",
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_open=self.on_open
        )
        
        ws_thread = threading.Thread(target=self.ws.run_forever)
        ws_thread.daemon = True
        ws_thread.start()

    def run(self):
        logging.info(f"\nStarting Order Block Analysis with Delta Exchange India...")
        logging.info(f"Trading Pair: {self.symbol}")
        logging.info(f"Timeframe: {self.timeframe}")
        logging.info(f"Initial Capital: ${self.initial_capital}")
        
        self.connect()
        
        try:
            while self.running:
                # Check if 7 days have passed
                if datetime.now() - self.start_time > timedelta(days=7):
                    logging.info("7 days have passed. Shutting down...")
                    self.running = False
                    break
                    
                time.sleep(1)
                
        except KeyboardInterrupt:
            logging.info("\nShutting down...")
            self.running = False
        finally:
            self.save_data()
            if self.ws:
                self.ws.close()

if __name__ == "__main__":
    ob = OrderBlocks()
    ob.run()