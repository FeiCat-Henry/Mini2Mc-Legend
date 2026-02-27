asd ={"x-1z-1.r",}

shi=10



function extractCoordinates(s)
    local x, z = s:match("x(%-?%d+)z(%-?%d+)%.r")
    if x and z then
        return tonumber(x), tonumber(z)
    end
    return nil
end


function bian(a, b)
    local a_ = a * 32
    local b_ = b * 32
    local x = 0
    local z = 0
    local chu = {}
    
    while z ~= 32 do
        table.insert(chu, {a_, b_})
        x = x + 1
        a_ = a_ + 1
        if x == 32 then
            a_ = a * 32
            b_ = b_ + 1
            z = z + 1
            x = 0
        end
    end
    
    return chu
end

-- 原有的 printChunkBlockIDs 函数
function printChunkBlockIDs(chunkX, chunkZ)
    local baseX = chunkX * 16
    local baseZ = chunkZ * 16
    
    -- 注意这里将 mc 的 Z 坐标取反（迷你世界朝北递增，MC朝北递减）
    local miniSafeZ = -baseZ
    Player:setPosition(0, baseX, 6, miniSafeZ)

    print("区"..baseX.."/"..baseZ)
    for y = 0, 255 do
        local ids = {}

        for dx = 0, 15 do
            for dz = 0, 15 do
                local miniX = baseX + dx
                -- 核心修复：MC Z轴不仅反转，而且方块偏移1格以完美对齐MC区块！
                local miniZ = -(baseZ + dz) - 1
                Player:setPosition(0, miniX, y, miniZ)
                local _, id = Block:getBlockID(miniX, y, miniZ)
                table.insert(ids, tostring(id))
            end
        end

        local compressed = ""
        local count = 1
        local prev = ids[1]

        for i = 2, #ids do
            if ids[i] == prev then
                count = count + 1
            else
                compressed = compressed .. count .. "-" .. prev .. "/"
                prev = ids[i]
                count = 1
            end
        end
        compressed = compressed .. count .. "-" .. prev

        print(compressed)
    end
end

local chunkQueue = {}
local globalTotalChunks = (asd and #asd or 0) * 1024
local globalProcessedChunks = 0
local isProcessing = true

-- 每次只处理一个区块 (16x16x256 = 65536次循环)，防止游戏完全卡死
function processNextChunk()
    if #chunkQueue == 0 then
        -- 尝试从总列表中加载下一个文件的区块列表（如 x26z-1.r）
        if asd and #asd > 0 then
            local filename = asd[1]
            local x, z = extractCoordinates(filename)
            if x and z then
                -- 将用户填入的“迷你世界大区坐标”自动转换为“Minecraft大区坐标”
                local mcX = x
                local mcZ = -z - 1
                local mcFilename = "x" .. mcX .. "z" .. mcZ .. ".r"

                -- 仅输出纯文件名（Minecraft坐标名称）提供给Python打包
                print(mcFilename)

                local coords = bian(mcX, mcZ)
                for _, coord in ipairs(coords) do
                    table.insert(chunkQueue, coord)
                end
                local currentTotalFiles = math.floor(globalTotalChunks / 1024)
                local currentFileIndex = currentTotalFiles - #asd + 1
                Chat:sendSystemMsg("#G[导出脚本] #W开始读取文件: " .. filename .. " (#Y" .. currentFileIndex .. " / " .. currentTotalFiles .. "#W)")
            end
            table.remove(asd, 1)
        else
            if isProcessing then
                print("【导出完毕！】所有区块均已处理完成！")
                Chat:sendSystemMsg("#R[导出脚本] #W【导出完毕！】所有地图区块均已处理完成！")
                isProcessing = false
            end
            return false
        end
    end    
    -- 处理队首的一个区块
    if #chunkQueue > 0 then
        local coord = table.remove(chunkQueue, 1)
        globalProcessedChunks = globalProcessedChunks + 1

        -- 每处理 16 个区块，或者处理到最后一个区块时，发一次进度给聊天栏
        if globalProcessedChunks % 16 == 0 or globalProcessedChunks == globalTotalChunks then
            local percent = 0
            if globalTotalChunks > 0 then
                percent = (globalProcessedChunks / globalTotalChunks) * 100
            end
            local formattedPercent = string.format("%.2f", percent)
            -- 移除无法显示的特殊方块符号，只留下纯净的文字百分比
            Chat:sendSystemMsg("#Y[全局进度] #W" .. formattedPercent .. "％")
        end

        printChunkBlockIDs(coord[1], coord[2])
        return true
    end
    return false
end

function uio()
    if not isProcessing then return end
    shi = shi - 1
    if shi <= 0 then
        shi = 4 -- 每4个游戏刻提取1个区块(约0.2秒一个)，防止单帧卡死时间过长
        processNextChunk()
    end
end

ScriptSupportEvent:registerEvent([=[Game.Run]=],uio)

