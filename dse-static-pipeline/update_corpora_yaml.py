import glob

files = sorted(glob.glob("./data/*.yml"))

combined_text = ""
for x in files:
    with open(x, "r", encoding="utf-8") as fp:
        combined_text += fp.read()

print(combined_text)

with open("tmp.yaml", "w", encoding="utf-8") as fp:
    fp.write(combined_text)
