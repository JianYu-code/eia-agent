"""产污系数数据库 — 从《排放源统计调查产排污核算方法和系数手册》提取
用于 Step 7（产污系数核对）和 Step 5（源强重算）的精确查表验证"""

import json
import re
from pathlib import Path
from app.database import async_session

COEFF_DATA = [
    # === 锅炉/火力发电 ===
    # 燃料类型, 行业, 工艺/规模, 污染物, 产污系数, 单位, 来源标准, 备注
    ("煤", "锅炉", "层燃炉/链条炉", "SO₂", 16.0, "kg/t煤", "HJ 953-2018", "S=1%折算"),
    ("煤", "锅炉", "层燃炉/链条炉", "颗粒物", 8.0, "kg/t煤", "HJ 953-2018", "A=10%折合（不含除尘）"),
    ("煤", "锅炉", "层燃炉/链条炉", "NOx", 3.5, "kg/t煤", "HJ 953-2018", "无低氮燃烧"),
    ("煤", "锅炉", "层燃炉/链条炉", "NOx", 2.0, "kg/t煤", "HJ 953-2018", "低氮燃烧"),
    ("煤", "锅炉", "流化床炉", "SO₂", 16.0, "kg/t煤", "HJ 953-2018", "S=1%折算"),
    ("煤", "锅炉", "流化床炉", "颗粒物", 20.0, "kg/t煤", "HJ 953-2018", "A=20%折合（不含除尘）"),
    ("煤", "锅炉", "流化床炉", "NOx", 2.5, "kg/t煤", "HJ 953-2018", "循环流化床，低氮"),
    ("煤", "锅炉", "煤粉炉", "SO₂", 17.0, "kg/t煤", "HJ 953-2018", "S=1%折算"),
    ("煤", "锅炉", "煤粉炉", "颗粒物", 14.0, "kg/t煤", "HJ 953-2018", "A=15%折合（不含除尘）"),
    ("煤", "锅炉", "煤粉炉", "NOx", 5.5, "kg/t煤", "HJ 953-2018", "无低氮燃烧"),
    ("生物质", "锅炉", "层燃炉", "SO₂", 0.34, "kg/t燃料", "HJ 953-2018", "S=0.03%折算"),
    ("生物质", "锅炉", "层燃炉", "颗粒物", 3.8, "kg/t燃料", "HJ 953-2018", "A=3%折合（不含除尘）"),
    ("生物质", "锅炉", "层燃炉", "NOx", 2.0, "kg/t燃料", "HJ 953-2018", "无低氮燃烧"),
    ("天然气", "锅炉", "燃气锅炉", "SO₂", 0.02, "kg/万m³", "HJ 953-2018", "S=10mg/m³"),
    ("天然气", "锅炉", "燃气锅炉", "NOx", 6.97, "kg/万m³", "HJ 953-2018", "低氮燃烧"),
    ("天然气", "锅炉", "燃气锅炉", "NOx", 18.71, "kg/万m³", "HJ 953-2018", "无低氮燃烧"),
    ("天然气", "锅炉", "燃气锅炉", "颗粒物", 0.95, "kg/万m³", "HJ 953-2018", ""),
    ("柴油", "锅炉", "燃油锅炉", "SO₂", 19.0, "kg/t油", "HJ 953-2018", "S=1%" ),
    ("柴油", "锅炉", "燃油锅炉", "NOx", 3.67, "kg/t油", "HJ 953-2018", ""),
    ("柴油", "锅炉", "燃油锅炉", "颗粒物", 0.26, "kg/t油", "HJ 953-2018", ""),

    # === VOCs典型行业 ===
    ("溶剂型涂料", "涂装", "喷漆", "VOCs", 600, "kg/t涂料", "HJ 984", "溶剂型，未处理"),
    ("水性涂料", "涂装", "喷漆", "VOCs", 100, "kg/t涂料", "HJ 984", "水性，未处理"),
    ("粉末涂料", "涂装", "静电喷涂", "VOCs", 10, "kg/t涂料", "HJ 984", ""),
    ("油墨", "印刷", "凹版印刷", "VOCs", 300, "kg/t油墨", "HJ 984", "溶剂型油墨"),
    ("油墨", "印刷", "柔版印刷", "VOCs", 5, "kg/t油墨", "HJ 984", "水性油墨"),

    # === 废水 ===
    ("生活源", "生活污水", "城镇居民", "COD", 79, "g/(人·d)", "HJ 884", ""),
    ("生活源", "生活污水", "城镇居民", "氨氮", 9.7, "g/(人·d)", "HJ 884", ""),
    ("生活源", "生活污水", "城镇居民", "总氮", 13.6, "g/(人·d)", "HJ 884", ""),
    ("生活源", "生活污水", "城镇居民", "总磷", 1.17, "g/(人·d)", "HJ 884", ""),
]

WASTE_DATA = [
    # HW类别, 废物代码, 废物名称, 产生源
    ("HW06", "900-409-06", "废有机溶剂与含有机溶剂废物", "含有机溶剂废活性炭、废溶剂桶、清洗废液"),
    ("HW08", "900-214-08", "废矿物油与含矿物油废物", "机械设备维修产生的废机油、废液压油"),
    ("HW08", "900-217-08", "废矿物油", "废润滑油、废切削液"),
    ("HW08", "900-210-08", "含油废水处理污泥", "隔油池污泥、含油废水处理浮渣"),
    ("HW12", "900-252-12", "染料、涂料废物", "喷漆废漆渣、废油漆桶、漆雾过滤棉"),
    ("HW13", "900-015-13", "有机树脂类废物", "废离子交换树脂、废吸附树脂"),
    ("HW17", "336-064-17", "表面处理废物", "电镀污泥、含重金属废液"),
    ("HW49", "900-039-49", "废活性炭", "吸附有机废气/有机溶剂的废活性炭"),
    ("HW49", "900-041-49", "含有或沾染危险废物的废弃包装物", "废化学品包装桶、废试剂瓶"),
    ("HW49", "900-044-49", "废铅蓄电池", "废弃的UPS电池、铅酸蓄电池"),
    ("HW50", "772-007-50", "废催化剂", "SCR脱硝废催化剂（含V₂O₅/TiO₂）、催化燃烧废催化剂"),
    ("HW29", "900-023-29", "含汞废物", "废含汞荧光灯管、废汞温度计"),
    ("HW31", "900-052-31", "含铅废物", "废铅渣、含铅除尘灰"),
    ("HW34", "900-349-34", "废酸", "废硫酸、废盐酸、废硝酸"),
    ("HW35", "900-399-35", "废碱", "废氢氧化钠溶液、含碱清洗废液"),
    ("HW18", "772-005-18", "焚烧处置残渣", "危险废物焚烧产生的飞灰、底渣"),
    ("HW36", "900-032-36", "石棉废物", "废石棉保温材料"),
    ("HW48", "321-026-48", "有色金属采选和冶炼废物", "铝灰、铜渣"),
]


async def init_coefficient_db():
    """初始化产污系数数据库（从内存数据建表）"""
    from sqlalchemy import text
    async with async_session() as db:
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS emission_coefficient (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fuel_type TEXT, industry TEXT, process TEXT,
                pollutant TEXT, coefficient REAL, unit TEXT,
                source TEXT, notes TEXT
            )
        """))
        # 检查是否已初始化
        r = await db.execute(text("SELECT COUNT(*) FROM emission_coefficient"))
        if (await r.scalar()) == 0:
            for row in COEFF_DATA:
                await db.execute(text(
                    "INSERT INTO emission_coefficient (fuel_type,industry,process,pollutant,coefficient,unit,source,notes) VALUES (:a,:b,:c,:d,:e,:f,:g,:h)"
                ), {"a": row[0], "b": row[1], "c": row[2], "d": row[3], "e": row[4], "f": row[5], "g": row[6], "h": row[7]})
            await db.commit()
        return True


async def init_waste_db():
    """初始化危废代码数据库"""
    from sqlalchemy import text
    async with async_session() as db:
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS hazardous_waste (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT, code TEXT, name TEXT,
                source_waste TEXT, keywords TEXT
            )
        """))
        r = await db.execute(text("SELECT COUNT(*) FROM hazardous_waste"))
        if (await r.scalar()) == 0:
            for row in WASTE_DATA:
                keywords = " ".join([row[0], row[1], row[2], row[3]])
                await db.execute(text(
                    "INSERT INTO hazardous_waste (category,code,name,source_waste,keywords) VALUES (:a,:b,:c,:d,:e)"
                ), {"a": row[0], "b": row[1], "c": row[2], "d": row[3], "e": keywords})
            await db.commit()
        return True


async def query_coefficient(fuel_type: str = "", pollutant: str = "", process: str = "") -> list[dict]:
    """查询产污系数"""
    from sqlalchemy import text
    async with async_session() as db:
        q = "SELECT * FROM emission_coefficient WHERE 1=1"
        params = {}
        if fuel_type: q += " AND fuel_type LIKE :f"; params["f"] = f"%{fuel_type}%"
        if pollutant: q += " AND pollutant LIKE :p"; params["p"] = f"%{pollutant}%"
        if process: q += " AND process LIKE :pr"; params["pr"] = f"%{process}%"
        q += " LIMIT 20"
        r = await db.execute(text(q), params)
        return [{"fuel_type": row[1], "industry": row[2], "process": row[3], "pollutant": row[4], "coefficient": row[5], "unit": row[6], "source": row[7], "notes": row[8]} for row in r.fetchall()]


async def query_waste(name: str = "", code: str = "", category: str = "") -> list[dict]:
    """查询危废代码"""
    from sqlalchemy import text
    async with async_session() as db:
        q = "SELECT * FROM hazardous_waste WHERE 1=1"
        params = {}
        if name: q += " AND keywords LIKE :n"; params["n"] = f"%{name}%"
        if code: q += " AND code LIKE :c"; params["c"] = f"%{code}%"
        if category: q += " AND category LIKE :cat"; params["cat"] = f"%{category}%"
        q += " LIMIT 20"
        r = await db.execute(text(q), params)
        return [{"category": row[1], "code": row[2], "name": row[3], "source_waste": row[4]} for row in r.fetchall()]
