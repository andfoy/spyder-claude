# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License

"""Test configuration."""

import os

# Make Spyder use a temporary configuration directory during tests
os.environ["SPYDER_PYTEST"] = "True"
