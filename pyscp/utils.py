#!/usr/bin/env python3

###############################################################################
# Module Imports
###############################################################################

import funcy
import logging
import blessings
import re
import time
import threading
import signal

###############################################################################
# Decorators
###############################################################################


decorator = funcy.decorator


@decorator
def listify(call, wrapper=list):
    return wrapper(call())


@decorator
def morph(call, catch_exc, raise_exc):
    try:
        return call()
    except catch_exc as error:
        raise raise_exc(error) from error


@decorator
def ignore(call, error=Exception, value=None):
    try:
        return call()
    except error:
        return value


@decorator
def log_errors(call, logger=print):
    try:
        return call()
    except Exception as error:
        logger(repr(error))
        raise(error)


@decorator
def log_calls(call, logger=print):
    logger('{}: {}, {}'.format(call._func.__name__, call._args, call._kwargs))
    return call()


@decorator
def decochain(call, *decs):
    fn = call._func
    for dec in reversed(decs):
        fn = dec(fn)
    return fn(*call._args, **call._kwargs)

###############################################################################


def split(text, delimeters):
    pattern = '|'.join(map(re.escape, delimeters))
    return re.split(pattern, text)


class ProgressBar:

    def __init__(self, title, max_value):
        self.title = title
        self.max_value = max_value
        self.value = 0
        self.term = blessings.Terminal()
        signal.signal(signal.SIGINT, self.exit)

    def start(self):
        self.finished = False
        self.time_started = time.time()
        threading.Thread(target=self.run).start()

    def update(self):
        with self.term.hidden_cursor():
            print(self.line() + '\r', end='')

    def line(self):
        filled = 40 * self.value / self.max_value
        parts = ' ▏▎▍▌▋▊▉'
        current = int(filled * len(parts)) % len(parts)
        bar = '█' * int(filled) + parts[current] + ' ' * 40
        tm = time.gmtime(time.time() - self.time_started)
        return '{} |{}| {:>3}% ({}:{:02}:{:02})   '.format(
            self.title,
            self.term.green(bar[:40]),
            100 * self.value // self.max_value,
            tm.tm_hour, tm.tm_min, tm.tm_sec)

    def run(self):
        while not self.finished:
            self.update()
            time.sleep(1)

    def stop(self):
        self.finished = True
        print(self.line())

    def exit(self, signum, frame):
        self.stop()
        raise KeyboardInterrupt


def pbar(it, title=None, max=None):
    max = len(it) if max is None else max
    title = '' if title is None else title + ' '
    bar = ProgressBar(title, max)
    bar.start()
    for i in it:
        yield i
        bar.value += 1
        bar.update()
    bar.stop()

###############################################################################


class LogCount:

    def __init__(self):
        self.count = 1

    def filter(self, record):
        record.count = self.count
        self.count += 1
        return True


def log_sql_debug():
    logger = logging.getLogger('peewee')
    logger.setLevel(logging.DEBUG)
    logger.addFilter(LogCount())
    term = logging.StreamHandler()
    term.setFormatter(logging.Formatter('{count} {message}', style='{'))
    logger.addHandler(term)


def default_logging(debug=False):
    term = logging.StreamHandler()
    file = logging.FileHandler('pyscp.log', mode='w', delay=True)
    if debug:
        term.setLevel(logging.DEBUG)
        file.setLevel(logging.DEBUG)
    else:
        term.setLevel(logging.INFO)
        file.setLevel(logging.WARNING)
    term.setFormatter(logging.Formatter('{message}', style='{'))
    file.setFormatter(
        logging.Formatter('{asctime} {levelname:8s} {message}', style='{'))
    logger = logging.getLogger('pyscp')
    logger.setLevel(logging.DEBUG)
    logger.addHandler(term)
    logger.addHandler(file)

###############################################################################
