import logging

__all__ = ['RankingLogger']


class RankingLogger:
    def __init__(self, name):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.DEBUG)
        
        if not self.logger.handlers:
            self.formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

            self.file_handler = logging.FileHandler('/var/log/firephenix/rankingsystem.log')
            self.file_handler.setFormatter(self.formatter)
            self.logger.addHandler(self.file_handler)

            self.console_handler = logging.StreamHandler()
            self.console_handler.setFormatter(self.formatter)
            self.logger.addHandler(self.console_handler)
        
        self.logger.propagate = False
    
    def get_logger(self):
        return self.logger
