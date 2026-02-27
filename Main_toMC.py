import anvil
import os
import re


kuai=0
def chus():
    global block_id_map
    block_id_map = {}
    
    # 从外部文件读取方块ID映射表
    try:
        with open('block_id_data.txt', 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and line.startswith('block_id_map'):
                    # 执行这一行代码来添加映射
                    exec(line)
    except Exception as e:
        print(f"读取block_id_data.txt时出错: {e}")
        block_id_map["0"] = "air"
        block_id_map["1"] = "bedrock"
        block_id_map["3"] = "water"
        block_id_map["4"] = "water"
        block_id_map["5"] = "lava"
        block_id_map["6"] = "lava"
        block_id_map["25"] = "stone"
        block_id_map["101"] = "dirt"


kuai=0

def extract_coordinates(s):
    numbers = re.findall(r'-?\d+', s)
    return (int(numbers[0]), int(numbers[1])) if len(numbers) >= 2 else None



def convert_block_id(custom_id):
    global kuai
    kuai=kuai+1
    """返回对照表中对应的 Minecraft 方块名和属性。如果没有，返回 ???"""
    try:
        return block_id_map[str(custom_id)]
    except:
        block_id_map[str(custom_id)]="dirt"
        return block_id_map[str(custom_id)]



dai=os.listdir()
#dai=["x-1z-1.r"]
def chuasd():
    global yan_se
    global schu
    global ming
    global asd
    global yui
    global bina_l
    global sgu_f
    global y
    global x,z,qwe,chu_yun,yuan_x,yuan_z,qi_x,qi_z,region


    yan_se={}
    schu=open("TEMPFILE","a")
    while dai[0][-1]!="r":
        del dai[0]
    ming=dai[0]
    del dai[0]
    print(ming)
    asd=open(ming, "r", encoding="utf-8")  # 使用latin-1编码可以处理二进制数据
    asd=asd.readlines()
    yui={}
    bina_l=0
    sgu_f=0
    y=0
    x,z=0,0
    qwe={}
    chu_yun=[]
    region=anvil.EmptyRegion(extract_coordinates(ming)[0],extract_coordinates(ming)[1])
    print(region)
    yuan_x=extract_coordinates(ming)[0]*512
    yuan_z=extract_coordinates(ming)[1]*512
    qi_x=0
    qi_z=0

def extract_numbers(s):
    try:
        # 去掉首字符并分割
        s = s.rstrip('\n')  # 支持末尾多个\n的情况（如"\n\n"）
        content=s[1:]
        num_strs=content.split('/')
        num1=int(num_strs[0])
        num2=int(num_strs[1])

        return (num1,num2)

    except:pass



def decompress_string(s):
    s=s.rstrip('\n')  # 支持末尾多个\n的情况（如"\n\n"）
    result=[]
    try:
        for segment in s.split('/'):
            count_str,value=segment.split('-')
            count=int(count_str)
            result.extend([value]*count)

        return result
    except:
        #print(s)
        iop=1/0


def zhu_hs():
    global yan_se
    global schu
    global ming
    global asd
    global yui
    global bina_l
    global sgu_f
    global y
    global x,z,qwe,chu_yun,yuan_x,yuan_z,qi_x,qi_z,region

    bina_l = 0

    try:
        chuasd()
        chus()
        while True:
            if asd[bina_l][0]=="空":
                sgu_f=0

            if sgu_f==1:
                if asd[bina_l][0]!="区":
                    y=y+1
                    dfg=decompress_string(asd[bina_l])
                    rty=0
                    try:
                        while True:
                            idx = x * 16 + z
                            region.set_block(anvil.Block('minecraft', convert_block_id(dfg[idx])),(qi_x+x),y,(qi_z+z))
                            rty=rty+1
                            z=z+1
                            if z==16:
                                z=0
                                x=x+1

                    except Exception as e:
                        #print(e)
                        #print(x)
                        x=0
                        z=0

            if asd[bina_l][0]=="区":
                schu.write(asd[bina_l])
                sgu_f=1
                qi_x=extract_numbers(asd[bina_l])[0]
                qi_z=extract_numbers(asd[bina_l])[1]
                #print(extract_numbers(asd[bina_l]))
                y=0
            bina_l=bina_l+1
            if bina_l%10000==0:
                print(bina_l)
    except Exception as e:
        print(bina_l)
        print("正在生成MCA文件"+str(kuai))
        print(e)
        region.save('r.'+str(extract_coordinates(ming)[0])+'.'+str(extract_coordinates(ming)[1])+'.mca')




print("=========================开始转换=========================")
while True:
    zhu_hs()
