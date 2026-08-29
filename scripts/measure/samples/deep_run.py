"""Наибольшая достижимая глубина рекурсии при заданном пределе.

Запуск: python3 deep_run.py <deep|deep_plain> <предел>
Модуль берётся из каталога самого этого файла.
"""
import importlib
import os
import sys

os.environ.setdefault("OUROBOROS_DEBUG_INFO", os.devnull)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

name, limit = sys.argv[1], int(sys.argv[2])
sys.setrecursionlimit(limit)
mod = importlib.import_module(name)
try:
    mod.descend(1)
except RecursionError:
    pass
print(f"{name}: предел рекурсии {limit}, наибольшая достигнутая глубина {mod.reached}")
