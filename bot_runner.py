#!/usr/bin/env python3

import time
import signal
import os
import sys
import argparse
import psutil
import subprocess
import platform
from app.config import Config
from app.rankingsystem.rankingsystem import RankingSystem
from app.utils.logger import RankingLogger

logging = RankingLogger(__name__).get_logger()

IS_WINDOWS = platform.system() == 'Windows'

class BotRunner:
    def __init__(self):
        self.ranksystem = RankingSystem()
    
    def run(self):
        """Main runner function"""
        logging.info("Starting bot runner...")
        with open(Config.PID_FILE, 'w') as f:
            f.write(str(os.getpid()))
        logging.debug(f"PID {os.getpid()} written to {Config.PID_FILE}")

        signal.signal(signal.SIGINT, self.shutdown)
        signal.signal(signal.SIGTERM, self.shutdown)

        self.ranksystem.run()

    def shutdown(self, signum=None, frame=None):
        """Shutdown the bot runner gracefully"""
        logging.info("Shutting down bot runner...")
        self.ranksystem.shutdown()
        remove_pid_file()


def is_running():
    """Check if the bot is already running by verifying PID file and process"""
    if not os.path.exists(Config.PID_FILE):
        return False
    
    try:
        with open(Config.PID_FILE, 'r') as f:
            pid = int(f.read().strip())
        
        if psutil.pid_exists(pid):
            process = psutil.Process(pid)
            if "python" in process.name().lower() or "bot_runner" in process.name().lower():
                return True
        
        logging.warning(f"Found stale PID file for PID {pid}. Process not running.")
        remove_pid_file()
        return False
    
    except (IOError, ValueError, psutil.NoSuchProcess, psutil.AccessDenied) as e:
        logging.error(f"Error checking if process is running: {e}")
        remove_pid_file()
        return False

def remove_pid_file():
    """Remove the PID file if it exists"""
    try:
        if os.path.exists(Config.PID_FILE):
            os.remove(Config.PID_FILE)
            logging.debug(f"Removed PID file {Config.PID_FILE}")
    except Exception as e:
        logging.error(f"Failed to remove PID file: {e}")

def start_bot():
    """Start the bot if it's not already running"""
    if is_running():
        logging.info("Bot is already running")
        return False
    
    logging.info("Starting bot...")
    
    if IS_WINDOWS:
        # Windows-specific process creation
        try:
            subprocess_flags = 0
            if hasattr(subprocess, 'DETACHED_PROCESS'):
                subprocess_flags = subprocess.DETACHED_PROCESS
            
            python_exe = 'pythonw' if os.path.exists(sys.exec_prefix + '\\pythonw.exe') else sys.executable
            
            process = subprocess.Popen(
                [python_exe, os.path.abspath(__file__)],
                creationflags=subprocess_flags,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE
            )
            
            with open(Config.PID_FILE, 'w') as f:
                f.write(str(process.pid))
                
            time.sleep(2)
            
            if is_running():
                logging.info(f"Bot started successfully with PID {process.pid}")
                return True
            else:
                logging.error("Failed to start bot process")
                return False
                
        except Exception as e:
            logging.error(f"Error starting bot: {e}")
            return False
    else:
        try:
            child_pid = os.fork()
            if child_pid == 0:
                os.setsid()
                runner = BotRunner()
                runner.run()
                sys.exit(0)
            else:
                time.sleep(2)
                if is_running():
                    logging.info("Bot started successfully")
                    return True
                else:
                    logging.error("Failed to start bot")
                    return False
        except Exception as e:
            logging.error(f"Error starting bot: {e}")
            return False

def stop_bot():
    """Stop the bot if it's running"""
    if not is_running():
        logging.info("Bot is not running")
        return True
    
    try:
        with open(Config.PID_FILE, 'r') as f:
            pid = int(f.read().strip())
        
        process = psutil.Process(pid)

        if IS_WINDOWS:
            process.terminate()
        else:
            os.kill(pid, signal.SIGTERM)
        
        max_wait = 60  # seconds
        for _ in range(max_wait):
            if not psutil.pid_exists(pid):
                logging.debug(f"Bot stopped (PID {pid})")
                remove_pid_file()
                return True
            time.sleep(1)
        
        if psutil.pid_exists(pid):
            if IS_WINDOWS:
                process.kill()
            else:
                os.kill(pid, signal.SIGKILL)
            logging.warning(f"Force killed bot process (PID {pid})")
            remove_pid_file()
            return True
    
    except (IOError, ValueError, psutil.NoSuchProcess, psutil.AccessDenied, ProcessLookupError) as e:
        logging.error(f"Error stopping bot: {e}")
        remove_pid_file()
        return False

def restart_bot():
    """Restart the bot"""
    stop_bot()
    time.sleep(60) 
    return start_bot()

def check_status():
    """Check if the bot is running and start it if not"""
    if is_running():
        try:
            with open(Config.PID_FILE, 'r') as f:
                pid = int(f.read().strip())
            logging.debug(f"Bot is running with PID {pid}")
            return True
        except (IOError, ValueError) as e:
            logging.error(f"Error reading PID file: {e}")
            return False
    else:
        logging.info("Bot is not running. Starting it now...")
        return start_bot()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ['start', 'stop', 'restart', 'status']:
        parser = argparse.ArgumentParser(description="Bot Runner - manage the bot process")
        parser.add_argument('action', choices=['start', 'stop', 'restart', 'status'], 
                            help='Action to perform')
        args = parser.parse_args()
        
        if args.action == 'start':
            if start_bot():
                print("Bot started successfully")
                sys.exit(0)
            else:
                print("Failed to start bot")
                sys.exit(1)
        
        elif args.action == 'stop':
            if stop_bot():
                print("Bot stopped successfully")
                sys.exit(0)
            else:
                print("Failed to stop bot")
                sys.exit(1)
        
        elif args.action == 'restart':
            if restart_bot():
                print("Bot restarted successfully")
                sys.exit(0)
            else:
                print("Failed to restart bot")
                sys.exit(1)
        
        elif args.action == 'status':
            if check_status():
                print("Bot is running")
                sys.exit(0)
            else:
                print("Bot is not running")
                sys.exit(1)
    else:
        runner = BotRunner()
        runner.run()