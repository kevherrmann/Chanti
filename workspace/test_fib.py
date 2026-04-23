import pytest
from fib import fibonacci

def test_fibonacci_zero():
    assert fibonacci(0) == 0

def test_fibonacci_eins():
    assert fibonacci(1) == 1

def test_fibonacci_zehn():
    assert fibonacci(10) == 55