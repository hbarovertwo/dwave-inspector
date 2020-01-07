# Copyright 2019 D-Wave Systems Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import absolute_import

import time
import logging
import threading
from wsgiref.simple_server import make_server, WSGIRequestHandler

try:
    import importlib.resources as importlib_resources
except ImportError:
    # use a backport for python_version < 3.7
    import importlib_resources

import requests
from flask import Flask, send_from_directory
from werkzeug.exceptions import NotFound

try:
    import dwave._inspectorapp as appdata
except ImportError:
    # TODO: demote to warning only and use a dummy server in this case
    raise RuntimeError("Can't use the Inspector without 'dwave-inspectorapp' "
                       "package. Consult the docs for install instructions.")

from dwave.inspector.storage import problem_store


# get local server/app logger
logger = logging.getLogger(__name__)

# suppress logging from Werkzeug
logging.getLogger('werkzeug').addHandler(logging.NullHandler(logging.DEBUG))


class LoggingStream(object):
    """Provide file-like interface to a logger."""

    def __init__(self, logger, level):
        self.logger = logger
        self.level = level

    def write(self, message):
        for line in message.split('\n'):
            if line:
                self.logger.log(self.level, line)

    def flush(self):
        pass

# stream interface to our local logger
logging_stream = LoggingStream(logger, logging.DEBUG)


class LoggingWSGIRequestHandler(WSGIRequestHandler):
    """WSGIRequestHandler subclass that logs to our logger, instead of to
    ``sys.stderr`` (as hardcoded in ``http.server.BaseHTTPRequestHandler``).
    """

    def log_message(self, format, *args):
        logger.info(format, *args)

    def get_stderr(self):
        return logging_stream


class WSGIAsyncServer(threading.Thread):
    """WSGI server container for a wsgi app that runs asynchronously (in a
    separate thread).
    """

    def __init__(self, host, port, app, daemon=False):
        super(WSGIAsyncServer, self).__init__(daemon=daemon)

        self.server = make_server(
            host, port, app, handler_class=LoggingWSGIRequestHandler)

    def run(self):
        self.server.serve_forever()

    def stop(self):
        self.server.shutdown()
        self.join()

    def _ensure_accessible(self, sleep=0.1, tries=100, timeout=10):
        """Ping the canary URL (app root) until the app becomes accessible."""

        canary = 'http://{}:{}/'.format(*self.server.server_address)

        for _ in range(tries):
            try:
                requests.get(canary, timeout=timeout).raise_for_status()
                return True
            except:
                time.sleep(sleep)

        return False

    def ensure_started(self):
        if not self.is_alive():
            self.start()
            self._ensure_accessible()

    def ensure_stopped(self):
        if self.is_alive():
            self.stop()


app = Flask(__name__, static_folder=None)

@app.route('/')
@app.route('/<path:path>')
def send_static(path='index.html'):
    with importlib_resources.path(appdata, 'build') as basedir:
        return send_from_directory(basedir, path)

@app.route('/mocks/test/<problem_id>.json')
@app.route('/mocks/sapi/problems/<problem_id>.json')
def send_problem(problem_id):
    try:
        return problem_store[problem_id]
    except KeyError:
        raise NotFound


app_server = WSGIAsyncServer(host='127.0.0.1', port=8000, app=app)
