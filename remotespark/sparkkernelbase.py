# Copyright (c) 2015  aggftw@gmail.com
# Distributed under the terms of the Modified BSD License.
import requests
from ipykernel.ipkernel import IPythonKernel

import remotespark.utils.configuration as conf
from remotespark.utils.log import Log
from remotespark.utils.utils import get_connection_string


class SparkKernelBase(IPythonKernel):
    run_command = "run"
    config_command = "config"
    sql_command = "sql"
    hive_command = "hive"

    def __init__(self, implementation, implementation_version, language, language_version, language_info,
                 kernel_conf_name, session_language, client_name, **kwargs):
        # Required by Jupyter - Override
        self.implementation = implementation
        self.implementation_version = implementation_version
        self.language = language
        self.language_version = language_version
        self.language_info = language_info

        # Override
        self.kernel_conf_name = kernel_conf_name
        self.session_language = session_language
        self.client_name = client_name

        super(SparkKernelBase, self).__init__(**kwargs)

        self.logger = Log(self.client_name)
        self.session_started = False
        self._fatal_error = None

        # Disable warnings for test env in HDI
        requests.packages.urllib3.disable_warnings()

        if not kwargs.get("testing", False):
            (username, password, url) = self._get_configuration()
            self.connection_string = get_connection_string(url, username, password)
            self._load_magics_extension()

    def do_execute(self, code, silent, store_history=True, user_expressions=None, allow_stdin=False):
        if self._fatal_error is not None:
            self._abort_with_fatal_error(self._fatal_error)

        subcommand, flags, code_to_run = self._parse_user_command(code)

        if subcommand == self.run_command:
            code_to_run = "%%spark\n{}".format(code_to_run)
            return self._run_starting_session(code_to_run, silent, store_history, user_expressions, allow_stdin)
        elif subcommand == self.sql_command:
            code_to_run = "%%spark -c sql\n{}".format(code_to_run)
            return self._run_starting_session(code_to_run, silent, store_history, user_expressions, allow_stdin)
        elif subcommand == self.hive_command:
            code_to_run = "%%spark -c hive\n{}".format(code_to_run)
            return self._run_starting_session(code_to_run, silent, store_history, user_expressions, allow_stdin)
        elif subcommand == self.config_command:
            restart_session = False

            if self.session_started:
                if "f" not in flags:
                    raise KeyError("A session has already been started. In order to modify the Spark configuration, "
                                   "please provide the '-f' flag at the beginning of the config magic:\n\te.g. `%config"
                                   " -f {}`\n\nNote that this will kill the current session and will create a new one "
                                   "with the configuration provided. All previously run commands in the session will be"
                                   " lost.")
                else:
                    restart_session = True

            code_to_run = "%%spark config {}".format(code_to_run)

            return self._run_restarting_session(code_to_run, silent, store_history, user_expressions, allow_stdin,
                                                restart_session)
        else:
            raise KeyError("Magic '{}' not supported.".format(subcommand))

    def do_shutdown(self, restart):
        # Cleanup
        self._delete_session()

        return self._do_shutdown_ipykernel(restart)

    def _load_magics_extension(self):
        register_magics_code = "%load_ext remotespark"
        self._execute_cell(register_magics_code, True, False, shutdown_if_error=True,
                           log_if_error="Failed to load the Spark magics library.")
        self.logger.debug("Loaded magics.")

    def _start_session(self):
        if not self.session_started:
            self.session_started = True

            add_session_code = "%spark add {} {} {} skip".format(
                self.client_name, self.session_language, self.connection_string)
            self._execute_cell(add_session_code, True, False, shutdown_if_error=True,
                               log_if_error="Failed to create a Livy session.")
            self.logger.debug("Added session.")

    def _delete_session(self):
        if self.session_started:
            code = "%spark cleanup"
            self._execute_cell_for_user(code, True, False)
            self.session_started = False

    def _run_starting_session(self, code, silent, store_history, user_expressions, allow_stdin):
        self._start_session()
        return self._execute_cell(code, silent, store_history, user_expressions, allow_stdin)

    def _run_restarting_session(self, code, silent, store_history, user_expressions, allow_stdin, restart):
        if restart:
            self._delete_session()

        res = self._execute_cell(code, silent, store_history, user_expressions, allow_stdin)

        if restart:
            self._start_session()

        return res

    def _get_configuration(self):
        try:
            credentials = getattr(conf, 'kernel_' + self.kernel_conf_name + '_credentials')()
            ret = (credentials['username'], credentials['password'], credentials['url'])
            for string in ret:
                assert string
            return ret
        except (KeyError, AssertionError):
            message = "Please set configuration for 'kernel_{}_credentials' to initialize Kernel.".format(
                self.kernel_conf_name)
            self._abort_with_fatal_error(message)

    def _parse_user_command(self, code):
        # Normalize 2 signs to 1
        if code.startswith("%%"):
            code = code[1:]

        # When no magic, return run command
        if not code.startswith("%"):
            code = "%{} {}".format(self.run_command, code)

        # Remove percentage sign
        code = code[1:]

        split_code = code.split(None, 1)
        subcommand = split_code[0].lower()
        flags = []
        rest = split_code[1]

        # Get all flags
        flag_split = rest.split(None, 1)
        while len(flag_split) >= 2 and flag_split[0].startswith("-"):
            flags.append(flag_split[0][1:].lower())
            rest = flag_split[1]
            flag_split = rest.split(None, 1)

        # flags to lower
        flags = [i.lower() for i in flags]

        return subcommand, flags, rest

    def _execute_cell(self, code, silent, store_history=True, user_expressions=None, allow_stdin=False,
                      shutdown_if_error=False, log_if_error=None):
        reply_content = self._execute_cell_for_user(code, silent, store_history, user_expressions, allow_stdin)

        if shutdown_if_error and reply_content[u"status"] == u"error":
            error_from_reply = reply_content[u"evalue"]
            if log_if_error is not None:
                message = "{}\nException details:\n\t\"{}\"".format(log_if_error, error_from_reply)
                self._abort_with_fatal_error(message)

        return reply_content

    def _execute_cell_for_user(self, code, silent, store_history=True, user_expressions=None, allow_stdin=False):
        return super(SparkKernelBase, self).do_execute(code, silent, store_history, user_expressions, allow_stdin)

    def _do_shutdown_ipykernel(self, restart):
        return super(SparkKernelBase, self).do_shutdown(restart)

    def _abort_with_fatal_error(self, message):
        self._fatal_error = message

        error = conf.fatal_error_suggestion().format(message)
        self.logger.error(error)
        self._send_error(error)

        raise ValueError(message)

    def _send_error(self, error):
        stream_content = {"name": "stderr", "text": error}
        self._ipython_send_error(stream_content)

    def _ipython_send_error(self, stream_content):
        self.send_response(self.iopub_socket, "stream", stream_content)
