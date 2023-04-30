# cmd_test_runner

A command-line program test runner implemented in Python.

# Documentation

## Command-line arguments for test_runner.py

### Output of `python test_runner.py --help`:
```
usage: test_runner.py [-h] [--list] [--filter FILTER] [--threads THREADS] [--shuffle] [--brief] [--color {yes,no,auto}] [--update] [--var VAR VALUE] [dirs ...]

Test runner.

positional arguments:
  dirs                  all directories to lookup for tests

options:
  -h, --help            show this help message and exit
  --list                list the names of all tests instead of running them
  --filter FILTER       run only the tests whose name matches the given regular expression
  --threads THREADS     maximum number of threads to use for running tests, -1 means none, 0 means all availables cpus (default: 0)
  --shuffle             randomize tests' orders
  --brief               only print test failures
  --color {yes,no,auto}
                        enable/disable colored output (default: auto)
  --update              run tests and update the expected results according
  --var VAR VALUE       declare a variable to be expanded in test files using the '$VAR' format
```

### Details

- `--filter` allow you to select a subset of discovered tests to run, update or list. It takes as an argument a regular expression with the Python `re` module syntax.

Example, if `test_runner --list` returns 
```
foo.bar.0
foo.bar.1
foo.baz.0
foo.baz.2
```
`test_runner --list --filter foo\.ba[rz]\.0` will return
```
foo.bar.0
foo.baz.0
```

## Test file syntax

All test files must use the `.test` extension to be discovered by the test runner.

### Example

```
; CMD $exe --version
; EXITCODE 0
; STDOUT
my_program version 1.2.3

; STDERR

```

This test will execute the program `$exe` with the argument `--version`. The variable `$exe` will be expanded to `my_program` if the option `--var exe my_program` is passed when calling test_runner.py.

The test will pass if and only if:
- The return code of the program is `0`
- The standard output is a valid text and it is equals to `my_program version 1.2.3↲`.
- The standard error output is empty.

### Fields

- `CMD`: the program to execute with the given arguments. Shell syntax is not supported however the command is split into arguments using Python `shlex.split()`. The program is executed using Python `subprocess.run(shlex.split(cmd), ...)`.
- `EXITCODE`: the expected return code from the command. This field is optional and by default `EXITCODE` is `0`.
- `STDOUT`: the expected standard output from the command. This field is optional and by default the standard output is **not checked**.
- `STDERR`: the expected standard error output from the command. This field is optional and by default the standard error output is **not checked**.

All these fields support variables using the Python `string.Template` syntax (i.e. `$variable_name`). The variables are registered using the command line option `--var VAR_NAME VAR_VALUE`.

# License

This project is licensed under the MIT license.

The file [diff_match_patch.py]() is licensed under the Apache License, Version 2.0 (the "License"), and comes from [https://github.com/google/diff-match-patch]().
