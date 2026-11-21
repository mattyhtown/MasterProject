"""CLI argument handling for `0dte-find`. Fails before any network call."""

import sys
from unittest.mock import patch

import pytest

from apex_sharpe import cli


@pytest.mark.parametrize("argv, code", [
    (["0dte-find", "SPX", "--chart"], 2),           # missing path
    (["0dte-find", "--chart", "--bogus"], 2),       # option where path expected
    (["0dte-find", "--bogus"], 2),                  # unknown option
    (["0dte-find", "SPX", "QQQ"], 2),               # extra positional
    (["0dte-find", "--chart", "out.jpg"], 2),       # chart must be PNG
    (["0dte-find", "--help"], 0),
])
def test_bad_or_help_arguments_exit_before_fetching(argv, code):
    with patch.object(sys, "argv", ["apex_sharpe", *argv]), \
         patch("apex_sharpe.data.unusual_whales.UnusualWhalesClient.option_contracts") as fetch:
        with pytest.raises(SystemExit) as exc:
            cli.main()
    assert exc.value.code == code
    fetch.assert_not_called()
