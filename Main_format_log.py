import re
#拆分

def a(s):
    s = s.strip()
    # 使用正则表达式去除时间戳和标签前缀
    result = re.sub(r'^\[\d{2}:\d{2}:\d{2}\]\[.*?\]\s*', '', s).strip()
    return result


chu=open("A\\"+"uio","a",encoding="utf-8")
asd=open("TriggerScript.log","r",encoding="utf-8")
asd=asd.readlines()
del asd[0]
zxc=0
while zxc < len(asd):
    qwe = a(asd[zxc])
    if qwe:
        if qwe[-1] == "r":
            chu = open("A\\" + qwe, "w", encoding="utf-8")
        else:
            chu.write(qwe + "\n")
    zxc = zxc + 1
