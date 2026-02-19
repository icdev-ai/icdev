# CUI // SP-CTI
import pathlib

ESC = chr(27)
Q = chr(39)

target = pathlib.Path(r"c:/Users/schuo/Downloads/ICDev/tools/cli_formatter.py")

with open(target, "w", encoding="utf-8") as f:
    f.write("written")

print("generator created")