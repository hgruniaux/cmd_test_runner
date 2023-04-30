from typing import List, Union
from pathlib import Path
from threading import Thread, Lock
from queue import Queue
from string import Template
import diff_match_patch as dmp_module
import subprocess
import random
import time
import shlex
import glob
import re
import os

variables = {}
use_colors = True
brief_mode = False
ran_tests_count = 0
failed_tests = []
RUNNER_LOCK = Lock()
CMD_REGEX = re.compile(r'^;\s+(\w+)(.*)(\n([^;]*))?$', re.M)
VAR_REGEX = re.compile(r'@{(\w+)}')

def time_to_string(time_in_ns: int):
    if time_in_ns > 3_600_000_000_000: # about hours
        return "{} h".format(time_in_ns * 1e-9)
    elif time_in_ns > 60_000_000_000: # about minutes
        return "{} min".format(time_in_ns * 1e-9)
    elif time_in_ns > 1_000_000_000: # about seconds
        return "{} s".format(time_in_ns * 1e-9)
    else:
        return "{} ms".format(time_in_ns * 1e-6)

def expand_variables(template: str):
    global variables
    return Template(template).safe_substitute(variables)

class Output:
    def __init__(self) -> None:
        self.buffer = ""
        global use_colors
        global brief_mode
        self.brief_mode = brief_mode
        self.use_colors = use_colors
    
    def color(self, str):
        if not self.use_colors:
            return
        self.buffer += "\x1b[" + str + "m"
        
    def reset_color(self):
        self.color("0")
        
    def append(self, text: str):
        self.buffer += text
        
    def begin_test_run(self, name: str):
        if self.brief_mode:
            return
        
        self.color("32")
        self.buffer += "[ RUN      ]"
        self.reset_color()
        self.buffer += " "
        self.buffer += name
        self.buffer += "\n"
        
    def end_test_run(self, name: str, execution_time_in_ns: int, failed: bool):
        if failed:
            self.color("31")
            self.buffer += "[  FAILED  ]"
        elif not self.brief_mode:
            self.color("32")
            self.buffer += "[       OK ]"
        else:
            return
        
        self.reset_color()
        self.buffer += " "
        self.buffer += name
        self.buffer += " ("
        self.buffer += time_to_string(execution_time_in_ns)
        self.buffer += ")"
    
    def end_test_run_exception(self, name: str, exception):
        self.append(str(exception))
        self.append("\n")
        self.color("31")
        self.append("[  FAILED  ]")
        self.reset_color()
        self.append(" exception occured when running {}".format(name))
    
    def _output_stdout_stderr_mismatch(self, stdout_or_stderr: str, expected: str, actual: str):
        self.buffer += "Unexpected {} output from test.\n".format(stdout_or_stderr)
        
        if self.use_colors:
            self.buffer += "Diff (red: expected, green: actual):\n"
            self.buffer += "------------------------------------------------------------\n"
            dmp = dmp_module.diff_match_patch()
            diff = dmp.diff_main(expected, actual)
            dmp.diff_cleanupSemantic(diff)
            
            for change in diff:
                type, content = change
                if type == -1:
                    self.color("31")
                elif type == 1:
                    self.color("32")
                self.buffer += content.replace('\n', '↲\n')
                self.reset_color()
                
            self.buffer += "------------------------------------------------------------\n"
        else:
            self.buffer += "Actual:\n"
            self.buffer += "------------------------------------------------------------\n"
            self.buffer += actual
            self.buffer += "------------------------------------------------------------\n"
            self.buffer += "Expected:\n"
            self.buffer += "------------------------------------------------------------\n"
            self.buffer += expected
            self.buffer += "------------------------------------------------------------\n"
    
    def output_stdout_mismatch(self, expected: str, actual: str):
        self._output_stdout_stderr_mismatch("stdout", expected, actual)
    
    def output_stderr_mismatch(self, expected: str, actual: str):
        self._output_stdout_stderr_mismatch("stdout", expected, actual)
    
    def output_exitcode_mismatch(self, expected: int, actual: int):
        self.buffer += "Unexpected exit code from test.\n"
        self.buffer += "  Actual: {}\n".format(actual)
        self.buffer += "Expected: {}\n".format(expected)
    
    def flush(self):
        if len(self.buffer) > 0:
            RUNNER_LOCK.acquire()
            print(self.buffer)
            RUNNER_LOCK.release()
            self.buffer = ""

class Test:
    def __init__(self, name: str, path: str, args: List[str], expected_stdout: Union[str,None] = None, expected_stderr: Union[str,None] = None, expected_exitcode: Union[int,None] = None) -> None:
        self.name = name
        self.path = path
        self.args = args
        self.expected_stdout = expected_stdout
        self.expected_stderr = expected_stderr
        self.expected_exitcode = expected_exitcode
        
        if self.expected_exitcode is None:
            self.expected_exitcode = 0
        
    @staticmethod
    def load_from_file(path: str, name: str):
        args = None
        expected_exitcode = None
        expected_stdout = None
        expected_stderr = None
        
        with open(path, 'r') as f:
            file_content = f.read()
            for match in CMD_REGEX.finditer(file_content):
                cmd = match.group(1).lower()
                arg = match.group(2).strip()
                content = match.group(4)
                
                if cmd == 'exitcode':
                    expected_exitcode = int(expand_variables(arg))
                elif cmd == 'cmd' or cmd == 'command':
                    args = shlex.split(expand_variables(arg))
                elif cmd == 'stdout':
                    expected_stdout = expand_variables(content)
                elif cmd == 'stderr':
                    expected_stderr = expand_variables(content)

        return Test(name, path, args, expected_stdout, expected_stderr, expected_exitcode)

    def generate_output(self) -> str:
        result = subprocess.run(self.args, capture_output=True, text=True)
        
        output = "; CMD {}\n".format(" ".join(self.args))
        output += "; EXITCODE {}\n".format(result.returncode)
        output += "; STDOUT\n{}\n".format(result.stdout)
        output += "; STDERR\n{}".format(result.stderr)
        return output

    def update(self):
        output = self.generate_output()
        with open(self.path, 'w') as f:
            f.write(output)

    def run(self) -> bool:
        output = Output()
        output.begin_test_run(self.name)
        
        # Test execution
        start = time.perf_counter_ns()
        result = subprocess.run(self.args, capture_output=True, text=True)
        end = time.perf_counter_ns()
        execution_time = (end - start)
        
        fail = False
        
        # Check if the test exit code is correct.
        if self.expected_exitcode != result.returncode:
            fail = True
            output.output_exitcode_mismatch(self.expected_exitcode, result.returncode)
                
        # Check if stdout output is correct.
        if self.expected_stdout is not None and self.expected_stdout != result.stdout:
            fail = True
            output.output_stdout_mismatch(self.expected_stdout, result.stdout)
        
        # Check if stderr output is correct.
        if self.expected_stderr is not None and self.expected_stderr != result.stderr:
            fail = True
            output.output_stderr_mismatch(self.expected_stderr, result.stderr)
        
        output.end_test_run(self.name, execution_time, fail)
        output.flush()
        return fail

class TestRunnerThread(Thread):
    def __init__(self, N: int, queue: Queue, updating_mode: bool):
        super().__init__(name="TestRunner-{}".format(N), daemon=True)
        self.queue = queue
        self.updating_mode = updating_mode
        
    def run(self) -> None:
        global ran_tests_count
        global failed_tests
        
        if self.updating_mode:
            while True:
                test = self.queue.get()
                try:
                    test.update()
                except Exception as e:
                    print("ERROR: exception occured when updating {}\n{}".format(test.name, str(e)))
                
                RUNNER_LOCK.acquire()
                ran_tests_count += 1
                RUNNER_LOCK.release()
                self.queue.task_done()
        else:
            while True:
                test = self.queue.get()
                
                failed = True
                try:
                    failed = test.run()
                except Exception as e:
                    output = Output()
                    output.end_test_run_exception(test.name, e)
                    output.flush()
                
                RUNNER_LOCK.acquire()
                ran_tests_count += 1
                if failed:
                    failed_tests.append(test)
                RUNNER_LOCK.release()
                
                self.queue.task_done()

class TestSuite:
    def __init__(self) -> None:
        self.tests = []
    
    def discover_tests(self, dir):
        for test_path in glob.glob(dir + "/**/*.test", recursive = True):
            abs_path = Path(dir, test_path)
            rel_path = abs_path.relative_to(dir)
            rel_path = rel_path.with_suffix('')
            name = str(rel_path.as_posix()).replace('/', '.')
            
            test = Test.load_from_file(abs_path, name)
            self.tests.append(test)
            
    def _run_tests(self, tests, threads_count: int, shuffle: bool, updating_mode: bool):        
        if shuffle:
            random.shuffle(tests)
            
        output = Output()
            
        global brief_mode
        if not brief_mode and not updating_mode:
            output.color("32")
            output.append("[==========]")
            output.reset_color()
            output.append(" Running {} tests".format(len(tests)))
        output.flush()
    
        global ran_tests_count
        global failed_tests
    
        if threads_count >= 0:
            start = time.perf_counter_ns()
        
            cpu_count = threads_count if threads_count > 0 else os.cpu_count()
        
            queue = Queue()
            threads = []
            for i in range(cpu_count):
                thread = TestRunnerThread(i, queue, updating_mode)
                thread.start()
                threads.append(thread)
                
            for test in tests:
                queue.put(test)
                
            queue.join()
            end = time.perf_counter_ns()
        else:
            if updating_mode:
                start = time.perf_counter_ns()
                for test in tests:
                    test.update()
                    ran_tests_count += 1
                end = time.perf_counter_ns()
            else:
                start = time.perf_counter_ns()
                for test in tests:
                    failed = test.run()
                    ran_tests_count += 1
                    if failed:
                        failed_tests.append(test)
                end = time.perf_counter_ns()
            
        if updating_mode:
            text = "1 UPDATED TEST" if ran_tests_count == 1 else "{} UPDATED TESTS".format(ran_tests_count)
            output.append("\n {}\n".format(text))
            output.flush()
            return
            
        execution_time = (end - start)
        
        passed_tests_count = ran_tests_count - len(failed_tests)
        
        output.color("32")
        output.append("[==========]")
        output.reset_color()
        output.append(" {} tests ran ({}).\n".format(ran_tests_count, time_to_string(execution_time)))
        
        text = "{} tests".format(passed_tests_count) if passed_tests_count > 1 else "{} test".format(passed_tests_count)
        output.color("32")
        output.append("[  PASSED  ]")
        output.reset_color()
        output.append(" {}.".format(text))
            
        if len(failed_tests) > 0:
            output.append("\n")
            text = "1 test" if len(failed_tests) == 1 else "{} tests".format(len(failed_tests))
            output.color("31")
            output.append("[  FAILED  ]")
            output.reset_color()
            output.append(" {}, listed below:\n".format(text))
            
            for failed_test in failed_tests:
                output.color("31")
                output.append("[  FAILED  ]")
                output.reset_color()
                output.append(" ")
                output.append(failed_test.name)
                output.append("\n")
            
            text = "1 FAILED TEST" if len(failed_tests) == 1 else "{} FAILED TESTS".format(len(failed_tests))
            output.append("\n {}\n".format(text))
            
        output.flush()
            
    def run_tests(self, regex_filter: Union[None, str, re.Pattern], threads_count: int = 0, shuffle: bool = False, updating_mode: bool = False):
        tests = self.tests
        if regex_filter is not None:
            tests = list(filter(lambda test: re.fullmatch(regex_filter, test.name) is not None, self.tests))
        self._run_tests(tests, threads_count=threads_count, shuffle=shuffle, updating_mode=updating_mode)
        
    def list_tests(self, regex_filter: Union[None, str, re.Pattern]):
        if regex_filter is not None:
            for test in filter(lambda test: re.fullmatch(regex_filter, test.name) is not None, self.tests):
                print(test.name)
        else:
            for test in self.tests:
                print(test.name)

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Test runner.')
    parser.add_argument('dirs', nargs='*', default=['.'], help="all directories too* lookup for tests")
    parser.add_argument('--list', action='store_true', help="list the names of all tests instead of running them")
    parser.add_argument('--filter', help="run only the tests whose name matches the given regular expression")
    parser.add_argument('--threads', type=int, default=0, help="maximum number of threads to use for running tests, -1 means none, 0 means all availables cpus (default: 0)")
    parser.add_argument('--shuffle', action='store_true', help="randomize tests' orders")
    parser.add_argument('--brief', action='store_true', help="only print test failures")
    parser.add_argument('--color', choices=['yes', 'no', 'auto'], default='auto', help="enable/disable colored output (default: auto)")
    parser.add_argument('--update', action='store_true', help="run tests and update the expected results according")
    parser.add_argument('--var', nargs=2, action='append', metavar=('VAR', 'VALUE'), help="declare a variable to be expanded in test files using the '$VAR' format")
    options = parser.parse_args()
    
    if options.var is not None:
        for var in options.var:
            variables[var[0]] = var[1]
    
    if options.color == 'auto':
        import sys
        if sys.stdout.isatty():
            use_colors = True
        else:
            use_colors = False
    elif options.color == 'yes':
        use_colors = True
    else:
        use_colors = False
    
    suite = TestSuite()
    for dir in options.dirs:
        suite.discover_tests(dir)
        
    if options.list:
        suite.list_tests(regex_filter=options.filter)
        exit(0)
        
    brief_mode = options.brief
    suite.run_tests(regex_filter=options.filter, threads_count=options.threads, shuffle=options.shuffle, updating_mode=options.update)
