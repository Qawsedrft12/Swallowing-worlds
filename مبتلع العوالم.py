import os
import sys
import sqlite3
import json
import random
import time
import uuid
import logging
import shlex
import re
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Tuple, Set, Union
from enum import Enum

# ---------------------------
# إعدادات الألوان للنصوص
# ---------------------------
class Colors:
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    END = '\033[0m'

# ---------------------------
# إعدادات عامة
# ---------------------------
DB_FILE = "nested_worlds_master_ar.db"
LOG_FILE = "nested_worlds_master_ar.log"
GAME_DATA_FILE = "game_data.json"
RANDOM_SEED = uuid.uuid4().int
AUTOSAVE_ON_EXIT = True

CONFIG = {
    "BACKGROUND_TICK": True,
    "TICK_INTERVAL_SEC": 10,
    "TICKS_PER_RUN": 1,
    "MAX_WORLD_TICKS_PER_RUN": 10,
    "SAVE_EVERY_RUN": True,
    "WORLD_POOL_MIN": 10,
    "MAX_CREATURES_PER_WORLD": 50,
    "MAX_SNAPSHOTS_PER_WORLD": 30,
    "CACHE_CLEANUP_INTERVAL": 300,
    "MIN_CREATURE_SPAWN": 2
}

random.seed(RANDOM_SEED)
logging.basicConfig(
    filename=LOG_FILE, 
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    encoding='utf-8'
)

def load_game_data(filename: str) -> Dict[str, Any]:
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logging.critical(f"FATAL: Could not load game data from {filename}: {e}")
        sys.exit(f"خطأ فادح: لا يمكن تحميل بيانات اللعبة من {filename}")

GAME_DATA = load_game_data(GAME_DATA_FILE)
BLOCKS = GAME_DATA["BLOCKS"]
CREATURES = GAME_DATA["CREATURES"]
PREDATION = GAME_DATA["PREDATION"]
DIET_TO_RESOURCES = GAME_DATA["DIET_TO_RESOURCES"]
BIOMES = GAME_DATA["BIOMES"]
RECIPES = GAME_DATA["RECIPES"]
ELEMENT_RELATIONSHIPS = GAME_DATA.get("ELEMENT_RELATIONSHIPS", {})
BUILDINGS = GAME_DATA.get("BUILDINGS", {})
HUMANS = GAME_DATA.get("HUMANS", {})
PROFESSIONS = GAME_DATA.get("PROFESSIONS", {})
SKILLS = GAME_DATA.get("SKILLS", {})
SKILL_EFFECTS = GAME_DATA.get("SKILL_EFFECTS", {})
CURRENCY = GAME_DATA.get("CURRENCY", {})

# ---------------------------
# دوال مساعدة
# ---------------------------
AR_FAKE_MAP = {'أ': 'ا', 'إ': 'ا', 'آ': 'ا', 'ى': 'ي', 'ئ': 'ي', 'ؤ': 'و', 'ة': 'ه', 'ٱ': 'ا'}

def normalize_ar_text(s: str) -> str:
    if not isinstance(s, str): return s
    s = s.strip().lower()
    for k,v in AR_FAKE_MAP.items():
        s = s.replace(k,v)
    s = re.sub(r'[^0-9a-z\u0600-\u06FF\s_:#@-]', '', s)
    s = re.sub(r'\s+', ' ', s)
    return s

def normalize_digits(s: str) -> str:
    arabic_digits = "٠١٢٣٤٥٦٧٨٩"
    western_digits = "0123456789"
    return s.translate(str.maketrans(arabic_digits, western_digits))

def parse_input_line(line: str):
    line = normalize_digits(line)
    try:
        parts = shlex.split(line)
    except Exception:
        parts = line.strip().split()
    return parts

def make_key(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:6]}"

def safe_filename(name: str) -> str:
    name = os.path.basename(name)
    if not name.lower().endswith('.json'):
        name += '.json'
    return name
    
def get_rarity(block_id: str) -> float:
    """الحصول على ندرة المورد، مع قيمة افتراضية إذا كانت البيانات غير صالحة."""
    block_data = BLOCKS.get(block_id)
    if not block_data or "rarity" not in block_data:
        logging.warning(f"Invalid block data for ID: {block_id}. Rarity not found.")
        return 1.0
    return block_data["rarity"]

def colored_text(text: str, color: str) -> str:
    """إضافة ألوان للنصوص لتحسين تجربة المستخدم"""
    return f"{color}{text}{Colors.END}"

# ---------------------------
# أنظمة تحقيق الإنجازات
# ---------------------------
class AchievementSystem:
    def __init__(self):
        self.achievements = {
            "first_ingestion": {"name": "أول ابتلاع", "desc": "ابتلاع أول عالم", "unlocked": False},
            "master_ingester": {"name": "سيد الابتلاع", "desc": "ابتلاع 10 عوالم", "unlocked": False, "count": 0},
            "ecosystem_balance": {"name": "توازن بيئي", "desc": "الحفاظ على 5 مخلوقات لمدة 10 تيكس", "unlocked": False},
            "craft_master": {"name": "سيد الصنعة", "desc": "صنع 5 عناصر مختلفة", "unlocked": False, "count": 0},
            "settlement_founder": {"name": "مؤسس المستوطنة", "desc": "إنشاء أول مستوطنة", "unlocked": False},
            "skill_master": {"name": "سيد المهارات", "desc": "الوصول لمستوى 10 في 3 مهارات", "unlocked": False},
            "trade_master": {"name": "سيد التجارة", "desc": "إجراء 50 صفقة تجارية", "unlocked": False, "count": 0},
            "settlement_ruler": {"name": "حاكم المستوطنات", "desc": "امتلاك 3 مستوطنات", "unlocked": False, "count": 0}
        }
    
    def check_achievement(self, achievement_id: str, progress: int = 1) -> bool:
        if achievement_id in self.achievements:
            achievement = self.achievements[achievement_id]
            if not achievement["unlocked"]:
                if "count" in achievement:
                    achievement["count"] += progress
                    if achievement_id == "master_ingester" and achievement["count"] >= 10:
                        achievement["unlocked"] = True
                        return True
                    elif achievement_id == "trade_master" and achievement["count"] >= 50:
                        achievement["unlocked"] = True
                        return True
                    elif achievement_id == "craft_master" and achievement["count"] >= 5:
                        achievement["unlocked"] = True
                        return True
                    elif achievement_id == "settlement_ruler" and achievement["count"] >= 3:
                        achievement["unlocked"] = True
                        return True
                else:
                    achievement["unlocked"] = True
                    return True
        return False
    
    def to_dict(self):
        return self.achievements
    
    @staticmethod
    def from_dict(data):
        system = AchievementSystem()
        if data:
            system.achievements = data
        return system

# ---------------------------
# نظام المهارات المحسن
# ---------------------------
class SkillSystem:
    def __init__(self):
        self.skill_xp_values = {
            "ingest": 5.0, "gather": 3.0, "craft": 8.0,
            "fight": 10.0, "build": 7.0, "trade": 6.0,
            "plant": 4.0, "harvest": 3.0, "work": 2.0
        }
    
    def get_skill_effect(self, skill_name: str, level: int) -> Dict[str, float]:
        """الحصول على تأثير المهارة بناء على المستوى من game_data.json"""
        skill_data = SKILL_EFFECTS.get(skill_name, {})
        effects = skill_data.get("effects", {})
        
        # العثور على التأثير المناسب للمستوى
        applicable_effects = {}
        for effect_level, effect_data in effects.items():
            if level >= int(effect_level):
                applicable_effects.update(effect_data)
        
        return applicable_effects
    
    def gain_skill_xp(self, skill_name: str, action_type: str, magnitude: float = 1.0) -> float:
        """اكتساب خبرة المهارة بناء على نوع العمل"""
        xp_per_action = self.skill_xp_values.get(action_type, 1.0)
        return xp_per_action * magnitude

# ---------------------------
# نظام المهن المحسن
# ---------------------------
class ProfessionSystem:
    def can_assign_profession(self, human_type: str, profession: str) -> bool:
        """التحقق من إمكانية تعيين المهنة للإنسان"""
        human_data = HUMANS.get(human_type, {})
        profession_data = PROFESSIONS.get(profession, {})
        
        if not human_data or not profession_data:
            return False
        
        # التحقق من المهارات المطلوبة
        required_skills = profession_data.get("required_skills", {})
        human_skills = human_data.get("skills", {})
        
        for skill, min_level in required_skills.items():
            if human_skills.get(skill, 0) < min_level:
                return False
        
        return True
    
    def get_available_professions(self, human_type: str) -> List[str]:
        """الحصول على المهن المتاحة لنوع الإنسان"""
        available = []
        for profession_id, profession_data in PROFESSIONS.items():
            if self.can_assign_profession(human_type, profession_id):
                available.append(profession_id)
        return available
    
    def get_profession_requirements(self, profession: str) -> Dict[str, int]:
        """الحصول على متطلبات المهنة"""
        profession_data = PROFESSIONS.get(profession, {})
        return profession_data.get("required_skills", {})
    
    def get_profession_production(self, profession: str, count: int) -> Dict[str, int]:
        """الحصول على إنتاج المهنة"""
        profession_data = PROFESSIONS.get(profession, {})
        production = profession_data.get("production", {})
        
        result = {}
        for item, amount in production.items():
            # تحويل القيمة إلى عدد صحيح إذا كانت نصاً
            amount_int = int(amount) if isinstance(amount, str) else amount
            result[item] = amount_int * count
        
        return result
    
    def get_profession_bonuses(self, profession: str, count: int) -> Dict[str, float]:
        """الحصول على مكافآت المهنة"""
        profession_data = PROFESSIONS.get(profession, {})
        bonuses = profession_data.get("bonuses", {})
        
        result = {}
        for bonus, value in bonuses.items():
            # تحويل القيمة إلى عدد عشري إذا كانت نصاً
            value_float = float(value) if isinstance(value, str) else value
            result[bonus] = value_float * count
        
        return result
    
    def calculate_productivity(self, profession: str, skill_levels: Dict[str, int]) -> float:
        """حساب إنتاجية المهنة بناء على مهارات الإنسان"""
        profession_data = PROFESSIONS.get(profession, {})
        base_productivity = profession_data.get("base_productivity", 1.0)
        
        # تطبيق تأثير المهارات
        skill_bonus = 1.0
        for skill, level in skill_levels.items():
            if skill in profession_data.get("productivity_skills", {}):
                skill_bonus += level * 0.1
        
        return base_productivity * skill_bonus

# ---------------------------
# نظام الاقتصاد المحسن
# ---------------------------
class RealEconomy:
    def __init__(self):
        self.base_prices = {
            "herb_common": 2, "dirt": 1, "mystic_moss": 3, 
            "ancient_wood": 5, "wood": 4, "stone": 3,
            "herb_medic": 8, "water": 2, "spirit_dust": 15,
            "ghoul_fungus": 20, "iron": 10, "gold": 50,
            "qi_crystal": 100, "obsidian": 8, "ice_crystal": 12,
            "sunstone": 200, "food": 3, "seeds": 1, "seeds_medic": 2,
            "spore_dust": 4, "compost": 2, "bones": 1, "essence": 25
        }
        self.player_wealth = {"spirit_coins": 200}
        self.market_demand = {}
        self.transaction_history = []
        self.trade_count = 0
        
    def calculate_price(self, item_id: str, quantity: int = 1, is_buying: bool = True) -> float:
        """حساب السعر مع مراعاة العرض والطلب"""
        base_price = self.base_prices.get(item_id, 1)
        demand = self.market_demand.get(item_id, 1.0)
        rarity = get_rarity(item_id)
        
        # سعر ديناميكي based على الندرة والطلب والكمية
        price = base_price * demand * (2 - rarity) * (0.9 + quantity * 0.01)
        
        # تعديل السعر حسب نوع العملية (شراء/بيع)
        if not is_buying:
            price *= 0.7  # سعر البيع أقل من سعر الشراء
        
        return round(price, 2)
    
    def update_demand(self, item_id: str, quantity: int, is_buying: bool):
        """تحديد الطلب بناء على حركة السوق"""
        current_demand = self.market_demand.get(item_id, 1.0)
        
        if is_buying:
            # الشراء يزيد الطلب
            demand_change = 0.1 * abs(quantity) / 10
        else:
            # البيع يقلل الطلب
            demand_change = -0.1 * abs(quantity) / 10
        
        self.market_demand[item_id] = max(0.5, min(2.0, current_demand + demand_change))
    
    def update_market(self):
        """تحديد السوق بناء على عوامل مختلفة"""
        for item_id in self.base_prices.keys():
            # تقليل الطلب تدريجياً مع الوقت
            current_demand = self.market_demand.get(item_id, 1.0)
            self.market_demand[item_id] = max(0.5, current_demand * 0.99)
        
        # أحداث عشوائية تؤثر على السوق
        if random.random() < 0.1:
            affected_item = random.choice(list(self.base_prices.keys()))
            change = random.uniform(0.8, 1.2)
            self.market_demand[affected_item] = max(0.5, min(2.0, 
                self.market_demand.get(affected_item, 1.0) * change))
    
    def get_market_info(self) -> str:
        """الحصول على معلومات عن السوق"""
        output = ["📊 حالة السوق:"]
        for item_id, demand in sorted(self.market_demand.items(), 
                                    key=lambda x: x[1], reverse=True)[:10]:
            item_name = BLOCKS.get(item_id, {}).get('name', item_id)
            base_price = self.base_prices.get(item_id, 1)
            current_price = self.calculate_price(item_id, 1, True)
            output.append(f"{item_name}: الطلب {demand:.2f} × السعر {current_price:.1f} (قاعدة: {base_price})")
        
        return "\n".join(output)
    
    def execute_trade(self, item_id: str, quantity: int, is_buying: bool, trading_skill: int = 1) -> Tuple[bool, float]:
        """تنفيذ صفقة تجارية"""
        price = self.calculate_price(item_id, quantity, is_buying)
        total_cost = price * quantity
        
        # تطبيق تأثير مهارة التجارة
        skill_system = SkillSystem()
        skill_effect = skill_system.get_skill_effect("trading", trading_skill)
        price_modifier = skill_effect.get("price_advantage", 1.0)
        total_cost = int(total_cost * price_modifier)
        
        if is_buying:
            if self.player_wealth["spirit_coins"] < total_cost:
                return False, total_cost
            self.player_wealth["spirit_coins"] -= total_cost
        else:
            self.player_wealth["spirit_coins"] += total_cost
        
        self.update_demand(item_id, quantity, is_buying)
        self.transaction_history.append({
            "item": item_id,
            "quantity": quantity,
            "price": price,
            "total": total_cost,
            "is_buying": is_buying,
            "timestamp": time.time()
        })
        
        self.trade_count += 1
        return True, total_cost
    
    def to_dict(self):
        return {
            "base_prices": self.base_prices,
            "market_demand": self.market_demand,
            "transaction_history": self.transaction_history,
            "player_wealth": self.player_wealth,
            "trade_count": self.trade_count
        }
    
    @staticmethod
    def from_dict(data):
        economy = RealEconomy()
        if data:
            economy.base_prices = data.get("base_prices", economy.base_prices)
            economy.market_demand = data.get("market_demand", {})
            economy.transaction_history = data.get("transaction_history", [])
            economy.player_wealth = data.get("player_wealth", {"spirit_coins": 100})
            economy.trade_count = data.get("trade_count", 0)
        return economy

# ---------------------------
# نظام التأثيرات الخاصة
# ---------------------------
class EffectSystem:
    def __init__(self):
        self.temporary_effects = {}
        self.permanent_effects = {}
        self.active_buffs = {}
        
    def apply_effect(self, effect_type: str, duration: float, value: float, source: str = ""):
        if duration <= 0:  # تأثير دائم
            self.permanent_effects[effect_type] = value
            return f"تأثير {effect_type} دائم مضاف!"
        else:
            expire_time = time.time() + duration
            self.temporary_effects[effect_type] = {
                "value": value,
                "expires": expire_time,
                "source": source
            }
            return f"تأثير {effect_type} مؤقت مضاف لمدة {duration:.1f} ثانية!"
    
    def update_effects(self):
        current_time = time.time()
        expired_effects = []
        
        for effect_type, effect_data in self.temporary_effects.items():
            if current_time >= effect_data["expires"]:
                expired_effects.append(effect_type)
        
        for effect in expired_effects:
            del self.temporary_effects[effect]
    
    def get_effect_value(self, effect_type: str) -> float:
        # الجمع بين التأثيرات الدائمة والمؤقتة
        permanent = self.permanent_effects.get(effect_type, 0)
        temporary = self.temporary_effects.get(effect_type, {}).get("value", 0)
        return permanent + temporary
    
    def get_active_effects(self) -> str:
        """الحصول على قائمة بالتأثيرات النشطة"""
        output = ["✨ التأثيرات النشطة:"]
        
        # التأثيرات الدائمة
        if self.permanent_effects:
            output.append("🔮 دائمة:")
            for effect, value in self.permanent_effects.items():
                effect_name = self._get_effect_name(effect)
                output.append(f"  {effect_name}: {value}")
        
        # التأثيرات المؤقتة
        if self.temporary_effects:
            output.append("⏳ مؤقتة:")
            current_time = time.time()
            for effect, data in self.temporary_effects.items():
                time_left = data["expires"] - current_time
                if time_left > 0:
                    effect_name = self._get_effect_name(effect)
                    output.append(f"  {effect_name}: {data['value']} (متبقي: {int(time_left)}s)")
        
        return "\n".join(output) if len(output) > 1 else "لا توجد تأثيرات نشطة"
    
    def _get_effect_name(self, effect_key: str) -> str:
        """ترجمة مفاتيح التأثيرات إلى أسماء عربية"""
        effect_names = {
            "ingestion_capacity": "سعة الابتلاع",
            "xp_gain": "زيادة الخبرة",
            "qi_generation": "توليد الطاقة",
            "plant_growth_boost": "تعزيز نمو النباتات",
            "attack_bonus": "تعزيز الهجوم",
            "defense_bonus": "تعزيز الدفاع",
            "crafting_speed": "سرعة الصنعة",
            "price_advantage": "ميزة السعر",
            "food_production": "إنتاج الغذاء",
            "population_capacity": "سعة السكان",
            "comfort": "الراحة",
            "spirit_attraction": "جذب الأرواح",
            "storage_capacity": "سعة التخزين",
            "item_preservation": "الحفاظ على العناصر",
            "trading_efficiency": "كفاءة التجارة",
            "training_speed": "سرعة التدريب"
        }
        return effect_names.get(effect_key, effect_key)
    
    def to_dict(self):
        return {
            "temporary_effects": self.temporary_effects,
            "permanent_effects": self.permanent_effects,
            "active_buffs": self.active_buffs
        }
    
    @staticmethod
    def from_dict(data):
        system = EffectSystem()
        if data:
            system.temporary_effects = data.get("temporary_effects", {})
            system.permanent_effects = data.get("permanent_effects", {})
            system.active_buffs = data.get("active_buffs", {})
        return system

# ---------------------------
# نظام البناء والهياكل
# ---------------------------
class BuildingSystem:
    def __init__(self):
        self.buildings = {}
        self.construction_queue = []
        self.last_construction_time = time.time()
        
    def can_build(self, building_id: str, resources: Dict[str, int]) -> bool:
        if building_id not in BUILDINGS:
            return False
        
        building_cost = BUILDINGS[building_id].get("cost", {})
        return all(resources.get(res, 0) >= amount for res, amount in building_cost.items())
    
    def get_available_buildings(self, resources: Dict[str, int]) -> List[str]:
        """الحصول على المباني المتاحة بناء على الموارد"""
        available = []
        for building_id, building_data in BUILDINGS.items():
            if self.can_build(building_id, resources):
                available.append(building_id)
        return available
    
    def get_building_info(self, building_id: str) -> str:
        """الحصول على معلومات عن مبنى"""
        if building_id not in BUILDINGS:
            return "المبنى غير موجود"
        
        building_data = BUILDINGS[building_id]
        cost = building_data.get("cost", {})
        effects = building_data.get("effects", {})
        
        output = [f"🏗️ {building_data.get('name', building_id)}:"]
        output.append("التكلفة:")
        for resource, amount in cost.items():
            resource_name = BLOCKS.get(resource, {}).get('name', resource)
            output.append(f"  {resource_name}: {amount}")
        
        output.append("التأثيرات:")
        for effect, value in effects.items():
            effect_name = self._get_effect_name(effect)
            output.append(f"  {effect_name}: {value}")
        
        return "\n".join(output)
    
    def _get_effect_name(self, effect_key: str) -> str:
        """ترجمة مفاتيح التأثيرات إلى أسماء عربية"""
        effect_names = {
            "food_production": "إنتاج الغذاء",
            "plant_growth_boost": "تعزيز نمو النباتات",
            "population_capacity": "سعة السكان",
            "comfort": "الراحة",
            "crafting_speed": "سرعة الصنعة",
            "crafting_quality": "جودة الصنعة",
            "qi_generation": "توليد الطاقة",
            "spirit_attraction": "جذب الأرواح",
            "storage_capacity": "سعة التخزين",
            "item_preservation": "الحفاظ على العناصر",
            "trading_efficiency": "كفاءة التجارة",
            "defense_bonus": "تعزيز الدفاع",
            "training_speed": "سرعة التدريب",
            "price_advantage": "ميزة السعر"
        }
        return effect_names.get(effect_key, effect_key)
    
    def construct_building(self, building_id: str, resources: Dict[str, int]) -> str:
        if not self.can_build(building_id, resources):
            return "لا تملك الموارد الكافية للبناء"
        
        building_cost = BUILDINGS[building_id].get("cost", {})
        for res, amount in building_cost.items():
            resources[res] = resources.get(res, 0) - amount
            if resources[res] <= 0:
                del resources[res]
        
        self.buildings[building_id] = self.buildings.get(building_id, 0) + 1
        building_data = BUILDINGS[building_id]
        
        return f"تم بناء {building_data['name']} بنجاح!"
    
    def get_building_effects(self) -> Dict[str, float]:
        effects = {}
        for building_id, count in self.buildings.items():
            if building_id in BUILDINGS:
                building_effects = BUILDINGS[building_id].get("effects", {})
                for effect, value in building_effects.items():
                    effects[effect] = effects.get(effect, 0) + value * count
        return effects
    
    def to_dict(self):
        return {
            "buildings": self.buildings,
            "construction_queue": self.construction_queue,
            "last_construction_time": self.last_construction_time
        }
    
    @staticmethod
    def from_dict(data):
        system = BuildingSystem()
        if data:
            system.buildings = data.get("buildings", {})
            system.construction_queue = data.get("construction_queue", [])
            system.last_construction_time = data.get("last_construction_time", time.time())
        return system

# ---------------------------
# نظام المستوطنات البشرية المحسن
# ---------------------------
class HumanSettlement:
    def __init__(self, name: str, population: int = 50):
        self.name = name
        self.population = population
        self.resources = {"food": 1000, "wood": 500, "stone": 300, "herb_common": 200}
        self.buildings = {"house": 10, "farm": 10, "workshop": 5}
        self.professions = {"farmer": 20, "woodcutter": 5, "miner": 5}
        self.culture_level = 1
        self.technology_level = 1
        self.last_development = time.time()
        self.defense = 1
        self.happiness = 100  # مستوى السعادة من 0-100
        
    def to_dict(self):
        return {
            "name": self.name,
            "population": self.population,
            "resources": self.resources,
            "buildings": self.buildings,
            "professions": self.professions,
            "culture_level": self.culture_level,
            "technology_level": self.technology_level,
            "last_development": self.last_development,
            "defense": self.defense,
            "happiness": self.happiness
        }
    
    @staticmethod
    def from_dict(data):
        settlement = HumanSettlement(data.get("name", "مستوطنة"))
        settlement.population = data.get("population", 50)
        settlement.resources = data.get("resources", {"food": 100, "wood": 50, "stone": 30})
        settlement.buildings = data.get("buildings", {"house": 10, "farm": 10,"workshop":5})
        settlement.professions = data.get("professions", {})
        settlement.culture_level = data.get("culture_level", 1)
        settlement.technology_level = data.get("technology_level", 1)
        settlement.last_development = data.get("last_development", time.time())
        settlement.defense = data.get("defense", 1)
        settlement.happiness = data.get("happiness", 100)
        return settlement
    
    def get_info(self) -> str:
        """الحصول على معلومات كاملة عن المستوطنة"""
        output = [
            f"🏘️ مستوطنة {self.name}:",
            f":{self.buildings} مبانى",
            f"👥 السكان: {self.population}",
            f"😊 السعادة: {self.happiness}/100",
            f"🛡️ الدفاع: {self.defense}",
            f"📚 الثقافة: {self.culture_level:.1f}",
            f"🔬 التكنولوجيا: {self.technology_level:.1f}"
        ]
        
        output.append("🏗️ المباني:")
        for building, count in self.buildings.items():
            building_name = BUILDINGS.get(building, {}).get('name', building)
            output.append(f"  {building_name}: {count}")
        
        output.append("👨‍🌾 المهن:")
        for profession, count in self.professions.items():
            profession_name = PROFESSIONS.get(profession, {}).get('name', profession)
            output.append(f"  {profession_name}: {count}")
        
        output.append("📦 الموارد:")
        for resource, amount in self.resources.items():
            resource_name = BLOCKS.get(resource, {}).get('name', resource)
            output.append(f"  {resource_name}: {amount}")
        
        return "\n".join(output)
    
    def update_settlement(self, current_time: float):
        """تحديث المستوطنة بشكل كامل"""
        # التأكد من أن last_development هو قيمة رقمية
        if not hasattr(self, 'last_development') or not isinstance(self.last_development, (int, float)):
            self.last_development = current_time - 36  # فرض التطور في المرة الأولى
            
        time_diff = current_time - self.last_development
        if time_diff < 36:  # تطور كل ساعة
            return
        
        # حساب عدد التيكات التي مرت
        ticks_passed = int(time_diff / 36)
        
        for _ in range(ticks_passed):
            self._develop_tick()
        
        self.last_development = current_time
    
    def _develop_tick(self):
        """تطوير المستوطنة في تيك واحد"""
        # زيادة السكان based على المساكن
        max_population = self.buildings.get("house", 0) * 5
        if self.population < max_population and self.resources.get("food", 0) > 30:
            growth_chance = 0.3 + (self.culture_level * 0.1)
            if random.random() < growth_chance:
                self.population += 1
                self.resources["food"] -= 1
        
        # إنتاج الموارد من المهن
        profession_system = ProfessionSystem()
        for profession, count in self.professions.items():
            prof_production = profession_system.get_profession_production(profession, count)
            for item, amount in prof_production.items():
                self.resources[item] = self.resources.get(item, 0) + amount
        
        # استهلاك الغذاء
        food_consumption = self.population * 0.5
        self.resources["food"] = max(0, self.resources.get("food", 0) - food_consumption)
        
        # تطوير ثقافي وتكنولوجي
        if self.resources.get("food", 0) > 80:
            self.culture_level += 0.01
            self.technology_level += 0.01
    
    def assign_profession(self, profession: str, count: int) -> str:
        """تعيين مهنة للسكان"""
        available_population = self.population - sum(self.professions.values())
        if count > available_population:
            return "لا يوجد عدد كافي من السكان المتاحين."
        
        self.professions[profession] = self.professions.get(profession, 0) + count
        return f"تم تعيين {count} سكان كمهنة {profession}"
    
    def get_production(self) -> Dict[str, int]:
        """الحصول على إنتاج المستوطنة بناء على المهن"""
        production = {}
        profession_system = ProfessionSystem()
        
        for profession, count in self.professions.items():
            prof_production = profession_system.get_profession_production(profession, count)
            for item, amount in prof_production.items():
                production[item] = production.get(item, 0) + amount
        
        return production
    
    def collect_resources(self, resource: str = None, amount: int = None) -> Dict[str, int]:
        """جمع الموارد من المستوطنة"""
        collected = {}
        
        if resource:
            # جمع مورد محدد
            available = self.resources.get(resource, 0)
            take = min(available, amount) if amount else available
            if take > 0:
                self.resources[resource] -= take
                collected[resource] = take
        else:
            # جمع جزء من جميع الموارد (ضريبة)
            for res, amt in self.resources.items():
                if amt > 10:  # فقط إذا كان هناك كمية كافية
                    take = int(amt * 0.1)  # 10% ضريبة
                    self.resources[res] -= take
                    collected[res] = take
        
        return collected

# ---------------------------
# Dataclasses
# ---------------------------
@dataclass
class CreatureInstance:
    uid: str
    spec_id: str
    energy: float
    age: int = 0
    last_breed: float = field(default_factory=time.time)
    last_access: float = field(default_factory=time.time)
    summoned: bool = False
    summon_expires: float = 0
    level_boost: int = 0

    def to_dict(self):
        return {
            "uid": self.uid, 
            "spec_id": self.spec_id, 
            "energy": self.energy, 
            "age": self.age,
            "last_breed": self.last_breed,
            "last_access": self.last_access,
            "summoned": self.summoned,
            "summon_expires": self.summon_expires,
            "level_boost": self.level_boost
        }

    @staticmethod
    def from_dict(d):
        creature = CreatureInstance(
            d["uid"], 
            d["spec_id"], 
            d.get("energy", 0.0), 
            d.get("age", 0)
        )
        creature.last_breed = d.get("last_breed", time.time())
        creature.last_access = d.get("last_access", time.time())
        creature.summoned = d.get("summoned", False)
        creature.summon_expires = d.get("summon_expires", 0)
        creature.level_boost = d.get("level_boost", 0)
        return creature

@dataclass
class World:
    id: str
    name: str
    seed: int
    size_cubes: int
    difficulty: float
    biome: str
    elements: Dict[str, int] = field(default_factory=dict)
    creatures: List[CreatureInstance] = field(default_factory=list)
    last_tick: float = field(default_factory=time.time)
    carrying_capacity: int = 0
    regen_rate_scalar: float = 1.0
    description: str = ""
    explored: bool = False
    last_access: float = field(default_factory=time.time)
    settlements: List[HumanSettlement] = field(default_factory=list)
    economy: RealEconomy = field(default_factory=RealEconomy)

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "seed": self.seed, "size_cubes": self.size_cubes,
            "difficulty": self.difficulty, "biome": self.biome, "explored": self.explored,
            "elements": self.elements, "creatures": [c.to_dict() for c in self.creatures],
            "last_tick": self.last_tick, "carrying_capacity": self.carrying_capacity,
            "regen_rate_scalar": self.regen_rate_scalar, "description": self.description,
            "last_access": self.last_access,
            "settlements": [s.to_dict() for s in self.settlements],
            "economy": self.economy.to_dict()
        }

    @staticmethod
    def from_dict(d):
        w = World(
            d["id"], d["name"], d["seed"], d["size_cubes"], 
            d["difficulty"], d.get("biome", "generic")
        )
        w.elements = d.get("elements", {})
        w.creatures = [CreatureInstance.from_dict(cd) for cd in d.get("creatures", [])]
        w.last_tick = d.get("last_tick", time.time())
        w.carrying_capacity = d.get("carrying_capacity", w.size_cubes // 10)
        w.regen_rate_scalar = d.get("regen_rate_scalar", 1.0)
        w.description = d.get("description", "")
        w.explored = d.get("explored", False)
        w.last_access = d.get("last_access", time.time())
        w.settlements = [HumanSettlement.from_dict(sd) for sd in d.get("settlements", [])]
        w.economy = RealEconomy.from_dict(d.get("economy", {}))
        return w

    def total_elements(self) -> int:
        return sum(self.elements.values())

    def total_creatures(self) -> int:
        return len(self.creatures)

# ---------------------------
# عالم مولد برمجياً
# ---------------------------
class WorldGenerator:
    @staticmethod
    def generate(seed: Optional[int] = None, size_tier: str = "medium") -> World:
        rnd = random.Random(seed if seed is not None else random.randint(1, 10**9))
        tier_map = {"small": (300,600), "medium": (700,1400), "large": (1500,2000)}
        if size_tier not in tier_map: size_tier = "medium"
        min_s, max_s = tier_map[size_tier]
        size = rnd.randint(min_s, max_s)
        
        biome_key = rnd.choice(list(BIOMES.keys()))
        biome_data = BIOMES[biome_key]
        biome_name = biome_data["name"]
        
        difficulty = round(0.8 + rnd.random() * 1.6, 2)
        wid = make_key(biome_key)
        name = f"{biome_name} #{wid.split('_')[-1]}"
        w = World(wid, name, rnd.randint(1, 10**9), size, difficulty, biome_key)
        
        w.carrying_capacity = max(5, int(size * (0.02 + (1.0 / (10 + difficulty)))))
        w.regen_rate_scalar = 1.0 + (size / 3000.0)
        if "effects" in biome_data and "regen_modifier" in biome_data["effects"]:
            w.regen_rate_scalar *= biome_data["effects"]["regen_modifier"]
            
        w.description = f"عالم نمط {biome_name}, حجم {size}, صعوبة {difficulty}"

        # توليد العناصر بناء على أوزان البايمز
        block_ids = list(biome_data["block_weights"].keys())
        block_weights = list(biome_data["block_weights"].values())
        
        total_weight = sum(block_weights)
        for block_id, weight in zip(block_ids, block_weights):
            count = max(1, int(size * weight / total_weight * rnd.uniform(0.8, 1.2)))
            w.elements[block_id] = w.elements.get(block_id, 0) + count

        # توليد المخلوقات
        creature_count = 0
        max_creatures = min(CONFIG["MAX_CREATURES_PER_WORLD"], size // 20)
        
        for spec_id, spawn_chance in biome_data["creature_spawns"].items():
            min_spawn = CONFIG["MIN_CREATURE_SPAWN"] if size > 800 and spawn_chance > 0.2 else 1 if size > 500 else 0
            
            base_count = max(min_spawn, int(size * 0.015 * spawn_chance / w.difficulty))
            count = rnd.randint(min_spawn, min(max_creatures - creature_count, base_count))
            
            if count > 0 and creature_count < max_creatures:
                spec = CREATURES[spec_id]
                for _ in range(count):
                    uid = f"{spec_id}_{uuid.uuid4().hex[:6]}"
                    energy = spec["energy_max"] * rnd.uniform(0.5, 0.9)
                    age = rnd.randint(0, int(spec["lifespan"] * 0.2))
                    inst = CreatureInstance(uid, spec_id, energy, age, time.time() - rnd.randint(0, 3600))
                    w.creatures.append(inst)
                    creature_count += 1
        
        # إضافة مستوطنة بشرية في العوالم الكبيرة
        if size > 1000 and rnd.random() < 0.3:
            settlement_name = f"قرية {biome_name} {rnd.randint(1, 100)}"
            settlement = HumanSettlement(settlement_name, rnd.randint(5, 15))
            w.settlements.append(settlement)
                    
        return w

# ---------------------------
# Storage: SQLite
# ---------------------------
class Storage:
    def __init__(self, dbfile=DB_FILE):
        self.dbfile = dbfile
        self.conn = sqlite3.connect(dbfile, timeout=30, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        try:
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA synchronous=NORMAL;")
            self.conn.execute("PRAGMA cache_size=-10000;")
        except Exception:
            pass
        self._init_schema()

    def _init_schema(self):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute("""CREATE TABLE IF NOT EXISTS worlds (id TEXT PRIMARY KEY, data_json TEXT)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS snapshots (key TEXT PRIMARY KEY, source_world TEXT, data_json TEXT, timestamp REAL)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS player (key TEXT PRIMARY KEY, data_json TEXT)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)""")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_snap_source ON snapshots(source_world);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_snap_timestamp ON snapshots(timestamp);")
            self.conn.commit()

    def save_world(self, w: World, commit: bool = True):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute("INSERT OR REPLACE INTO worlds (id, data_json) VALUES (?, ?)",
                        (w.id, json.dumps(w.to_dict(), ensure_ascii=False)))
            if commit: self.conn.commit()

    def load_world(self, wid: str) -> Optional[World]:
        with self.lock:
            cur = self.conn.cursor()
            cur.execute("SELECT data_json FROM worlds WHERE id=?", (wid,))
            r = cur.fetchone()
            if not r: return None
            try:
                return World.from_dict(json.loads(r["data_json"]))
            except Exception:
                logging.error(f"Failed to load world {wid}")
                return None

    def list_worlds(self) -> List[str]:
        with self.lock:
            cur = self.conn.cursor()
            cur.execute("SELECT id FROM worlds")
            return [r["id"] for r in cur.fetchall()]

    def delete_world(self, wid: str, commit: bool = True):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute("DELETE FROM worlds WHERE id=?", (wid,))
            if commit: self.conn.commit()

    def save_snapshot(self, key: str, source_world: str, data: dict, commit: bool = True):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute("INSERT OR REPLACE INTO snapshots (key, source_world, data_json, timestamp) VALUES (?,?,?,?)",
                        (key, source_world, json.dumps(data, ensure_ascii=False), time.time()))
            if commit: self.conn.commit()

    def load_snapshot(self, key: str) -> Optional[dict]:
        with self.lock:
            cur = self.conn.cursor()
            cur.execute("SELECT data_json, source_world, timestamp FROM snapshots WHERE key=?", (key,))
            r = cur.fetchone()
            if not r: return None
            return {"key": key, "source_world": r["source_world"], "data": json.loads(r["data_json"]), "timestamp": r["timestamp"]}

    def list_snapshots(self) -> List[dict]:
        with self.lock:
            cur = self.conn.cursor()
            cur.execute("SELECT key, source_world, timestamp FROM snapshots ORDER BY timestamp DESC")
            return [{"key": r["key"], "source_world": r["source_world"], "timestamp": r["timestamp"]} for r in cur.fetchall()]

    def delete_snapshot(self, key: str, commit: bool = True):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute("DELETE FROM snapshots WHERE key=?", (key,))
            if commit: self.conn.commit()
    
    def cleanup_snapshots_keep_recent(self, keep_n: int = CONFIG["MAX_SNAPSHOTS_PER_WORLD"]) -> int:
        with self.lock:
            cur = self.conn.cursor()
            cur.execute("SELECT DISTINCT source_world FROM snapshots")
            worlds_with_snaps = [row['source_world'] for row in cur.fetchall()]
            
            deleted_count = 0
            for wid in worlds_with_snaps:
                cur.execute("SELECT key FROM snapshots WHERE source_world=? ORDER BY timestamp DESC", (wid,))
                keys = [row['key'] for row in cur.fetchall()]
                if len(keys) > keep_n:
                    keys_to_delete = keys[keep_n:]
                    for key in keys_to_delete:
                        cur.execute("DELETE FROM snapshots WHERE key=?", (key,))
                    deleted_count += len(keys_to_delete)
            self.conn.commit()
            return deleted_count

    def save_player(self, player: dict, commit: bool = True):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute("INSERT OR REPLACE INTO player (key, data_json) VALUES (?, ?)", ("player", json.dumps(player, ensure_ascii=False)))
            if commit: self.conn.commit()

    def load_player(self) -> Optional[dict]:
        with self.lock:
            cur = self.conn.cursor()
            cur.execute("SELECT data_json FROM player WHERE key='player'")
            r = cur.fetchone()
            if not r: return None
            try:
                return json.loads(r["data_json"])
            except Exception:
                return None

    def export_all(self, filename: str) -> str:
        with self.lock:
            data = {"worlds": {}, "snapshots": [], "player": None}
            for wid in self.list_worlds():
                w = self.load_world(wid)
                data["worlds"][wid] = w.to_dict() if w else None
            data["snapshots"] = self.list_snapshots()
            pl = self.load_player()
            if pl: data["player"] = pl
            fname = safe_filename(filename)
            with open(fname, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return fname

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

# ---------------------------
# اللاعب وحالته المحسنة
# ---------------------------
@dataclass
class InnerWorld:
    id: str = "inner_hero"
    name: str = "عالمك الداخلي"
    size_cubes: int = 0
    capacity_cubes: int = 10000
    ingested_keys: List[str] = field(default_factory=list)
    elements: Dict[str,int] = field(default_factory=dict)
    creatures: List[CreatureInstance] = field(default_factory=list)
    mounts: Dict[str,str] = field(default_factory=dict)
    last_tick: float = field(default_factory=time.time)
    qi_generation_rate: float = 0.0
    stable_ecosystem_ticks: int = 0
    buildings: BuildingSystem = field(default_factory=BuildingSystem)
    settlements: List[HumanSettlement] = field(default_factory=list)
    effect_system: EffectSystem = field(default_factory=EffectSystem)

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "size_cubes": self.size_cubes, 
            "capacity_cubes": self.capacity_cubes, "ingested_keys": self.ingested_keys, 
            "elements": self.elements, "creatures": [c.to_dict() for c in self.creatures], 
            "mounts": self.mounts, "last_tick": self.last_tick,
            "qi_generation_rate": self.qi_generation_rate,
            "stable_ecosystem_ticks": self.stable_ecosystem_ticks,
            "buildings": self.buildings.to_dict(),
            "settlements": [s.to_dict() for s in self.settlements],
            "effect_system": self.effect_system.to_dict()
        }

    @staticmethod
    def from_dict(d):
        iw = InnerWorld(d.get("id","inner_hero"), d.get("name","عالمك الداخلي"))
        iw.size_cubes = d.get("size_cubes",0)
        iw.capacity_cubes = d.get("capacity_cubes",10000)
        iw.ingested_keys = d.get("ingested_keys",[])
        iw.elements = d.get("elements",{})
        iw.creatures = [CreatureInstance.from_dict(cd) for cd in d.get("creatures",[])]
        iw.mounts = d.get("mounts",{})
        iw.last_tick = d.get("last_tick", time.time())
        iw.qi_generation_rate = d.get("qi_generation_rate", 0.0)
        iw.stable_ecosystem_ticks = d.get("stable_ecosystem_ticks", 0)
        iw.buildings = BuildingSystem.from_dict(d.get("buildings", {}))
        iw.settlements = [HumanSettlement.from_dict(sd) for sd in d.get("settlements", [])]
        iw.effect_system = EffectSystem.from_dict(d.get("effect_system", {}))
        return iw

@dataclass
class Player:
    name: str = "البطل"
    level: int = 1
    xp: float = 0.0
    qi: float = 200.0
    hp: float = 1000.0
    inner: InnerWorld = field(default_factory=InnerWorld)
    inventory: Dict[str,int] = field(default_factory=dict)
    skills: Dict[str, int] = field(default_factory=lambda: {
        "ingestion_efficiency": 1,
        "qi_mastery": 1,
        "cultivation": 1,
        "combat": 1,
        "building": 1,
        "trading": 1,
        "gathering": 1,
        "crafting": 1
    })
    skill_experience: Dict[str, float] = field(default_factory=dict)
    last_active: float = field(default_factory=time.time)
    discovered_worlds: List[str] = field(default_factory=list)
    achievements: AchievementSystem = field(default_factory=AchievementSystem)
    ingested_worlds_count: int = 0
    crafted_items_count: int = 0
    economy: RealEconomy = field(default_factory=RealEconomy)
    effect_system: EffectSystem = field(default_factory=EffectSystem)
    reputation: Dict[str, int] = field(default_factory=lambda: {
        "traders_guild": 0,
        "spirit_council": 0,
        "forest_guardians": 0
    })

    def ingest_capacity(self) -> int:
        base_capacity = 3 * self.level
        efficiency_bonus = base_capacity * (0.1 * (self.skills.get("ingestion_efficiency", 1) - 1))
        capacity_boost = self.effect_system.get_effect_value("ingestion_capacity")
        return max(1, int(base_capacity + efficiency_bonus + capacity_boost))

    def gain_xp(self, amount: float) -> bool:
        xp_multiplier = 1.0 + self.effect_system.get_effect_value("xp_gain")
        actual_amount = amount * xp_multiplier
        self.xp += actual_amount
        leveled = False
        xp_needed = 100 * (1.5 **(self.level - 1))
        while self.xp >= xp_needed:
            self.xp -= xp_needed
            self.level += 1
            self.qi = min(5000.0, self.qi + 60)
            self.hp = min(500.0, self.hp + 10)
            leveled = True
            xp_needed = 100 * (1.5 **(self.level - 1))
        return leveled

    def gain_skill_xp(self, skill_name: str, xp_amount: float) -> Optional[str]:
        """اكتساب خبرة المهارة والترقية"""
        if skill_name not in self.skill_experience:
            self.skill_experience[skill_name] = 0.0
        
        self.skill_experience[skill_name] += xp_amount
        current_level = self.skills.get(skill_name, 1)
        
        # حساب الخبرة المطلوبة للترقية
        xp_needed = current_level * 100
        
        if self.skill_experience[skill_name] >= xp_needed and current_level < 20:
            self.skills[skill_name] = current_level + 1
            self.skill_experience[skill_name] = 0
            return f"🎉 ارتقيت في مهارة {self.get_skill_name(skill_name)} إلى المستوى {current_level + 1}!"
        
        return None

    def get_skill_name(self, skill_key: str) -> str:
        """ترجمة مفاتيح المهارات إلى أسماء عربية"""
        skill_names = {
            "ingestion_efficiency": "كفاءة الابتلاع",
            "qi_mastery": "إتقان الطاقة",
            "cultivation": "الزراعة",
            "combat": "القتال",
            "building": "البناء",
            "trading": "التجارة",
            "gathering": "الجمع",
            "crafting": "الصنعة"
        }
        return skill_names.get(skill_key, skill_key)

    def auto_use_items(self) -> str:
        """استخدام العناصر تلقائياً عند الحاجة"""
        # استخدم جرعات Qi عند انخفاض الطاقة
        if self.qi < 100 and "qi_potion_small" in self.inventory and self.inventory["qi_potion_small"] > 0:
            self.qi += 50
            self.inventory["qi_potion_small"] -= 1
            if self.inventory["qi_potion_small"] <= 0:
                del self.inventory["qi_potion_small"]
            return "استخدمت جرعة Qi صغيرة تلقائياً"
        
        # استخدم الجرعات المتوسطة عند انخفاض الطاقة الشديد
        if self.qi < 50 and "qi_potion_medium" in self.inventory and self.inventory["qi_potion_medium"] > 0:
            self.qi += 120
            self.inventory["qi_potion_medium"] -= 1
            if self.inventory["qi_potion_medium"] <= 0:
                del self.inventory["qi_potion_medium"]
            return "استخدمت جرعة Qi متوسطة تلقائياً"
        
        # ترقية السعة عند الاقتراب من الحد الأقصى
        if (self.inner.size_cubes / self.inner.capacity_cubes > 0.85 and 
            "capacity_upgrade" in self.inventory and self.inventory["capacity_upgrade"] > 0):
            self.inner.capacity_cubes += 1000
            self.inventory["capacity_upgrade"] -= 1
            if self.inventory["capacity_upgrade"] <= 0:
                del self.inventory["capacity_upgrade"]
            return "تمت ترقية سعة العالم الداخلي تلقائياً"
        
        return ""

    def update_reputation(self, faction: str, amount: int):
        """تحديد سمعة اللاعب مع فصيل"""
        if faction not in self.reputation:
            self.reputation[faction] = 0
        
        self.reputation[faction] += amount
        # تحديد الحدود الدنيا والعليا للسمعة
        self.reputation[faction] -= amount

    def get_reputation_effects(self) -> Dict[str, float]:
        """الحصول على تأثيرات السمعة"""
        effects = {}
        
        # تأثير سمعة نقابة التجار
        traders_rep = self.reputation.get("traders_guild", 0)
        if traders_rep > 0:
            effects["price_advantage"] = 1.0 + (traders_rep / 100) * 0.2
        
        # تأثير سمعة مجلس الأرواح
        spirit_rep = self.reputation.get("spirit_council", 0)
        if spirit_rep > 0:
            effects["qi_generation"] = 1.0 + (spirit_rep / 100) * 0.3
        
        # تأثير سمعة حراس الغابة
        forest_rep = self.reputation.get("forest_guardians", 0)
        if forest_rep > 0:
            effects["plant_growth_boost"] = 1.0 + (forest_rep / 100) * 0.25
        
        return effects

    def show_reputation(self) -> str:
        """عرض سمعة اللاعب"""
        output = ["🎭 سمعتك:"]
        
        faction_names = {
            "traders_guild": "نقابة التجار",
            "spirit_council": "مجلس الأرواح",
            "forest_guardians": "حراس الغابة"
        }
        
        for faction, rep in self.reputation.items():
            faction_name = faction_names.get(faction, faction)
            status = "🔴 عداء" if rep < -50 else "🟡 محايد" if rep < 50 else "🟢 صداقة"
            output.append(f"{faction_name}: {rep} {status}")
        
        # عرض تأثيرات السمعة
        effects = self.get_reputation_effects()
        if effects:
            output.append("\nتأثيرات السمعة:")
            for effect, value in effects.items():
                effect_name = self._get_effect_name(effect)
                output.append(f"  {effect_name}: {value:.2f}x")
        
        return "\n".join(output)

    def _get_effect_name(self, effect_key: str) -> str:
        """ترجمة مفاتيح التأثيرات إلى أسماء عربية"""
        effect_names = {
            "price_advantage": "ميزة السعر",
            "qi_generation": "توليد الطاقة",
            "plant_growth_boost": "تعزيز نمو النباتات"
        }
        return effect_names.get(effect_key, effect_key)

    def to_dict(self):
        return {
            "name": self.name, "level": self.level, "xp": self.xp, "qi": self.qi, "hp": self.hp,
            "inner": self.inner.to_dict(), "inventory": self.inventory, "skills": self.skills,
            "skill_experience": self.skill_experience,
            "last_active": self.last_active, "discovered_worlds": self.discovered_worlds,
            "achievements": self.achievements.to_dict(), "ingested_worlds_count": self.ingested_worlds_count,
            "crafted_items_count": self.crafted_items_count,
            "economy": self.economy.to_dict(),
            "effect_system": self.effect_system.to_dict(),
            "reputation": self.reputation
        }

    @staticmethod
    def from_dict(d):
        p = Player()
        p.name = d.get("name","البطل")
        p.level = d.get("level",1)
        p.xp = d.get("xp",0.0)
        p.qi = d.get("qi",200.0)
        p.hp = d.get("hp",1000.0)
        p.inner = InnerWorld.from_dict(d.get("inner",{}))
        p.inventory = d.get("inventory",{})
        p.skills = d.get("skills", {
            "ingestion_efficiency": 1, 
            "qi_mastery": 1, 
            "cultivation": 1,
            "combat": 1,
            "building": 1,
            "trading": 1,
            "gathering": 1,
            "crafting": 1
        })
        p.skill_experience = d.get("skill_experience", {})
        p.last_active = d.get("last_active", time.time())
        p.discovered_worlds = d.get("discovered_worlds", [])
        p.achievements = AchievementSystem.from_dict(d.get("achievements"))
        p.ingested_worlds_count = d.get("ingested_worlds_count", 0)
        p.crafted_items_count = d.get("crafted_items_count", 0)
        p.economy = RealEconomy.from_dict(d.get("economy", {}))
        p.effect_system = EffectSystem.from_dict(d.get("effect_system", {}))
        p.reputation = d.get("reputation", {
            "traders_guild": 0,
            "spirit_council": 0,
            "forest_guardians": 0
        })
        return p

# ---------------------------
# المحرك Engine المحسن
# ---------------------------
class Engine:
    def __init__(self):
        self.storage = Storage(DB_FILE)
        self.player = self._load_or_new_player()
        self._ensure_world_pool()
        self.lock = threading.RLock()
        self._cached_worlds: Dict[str, World] = {}
        self._cache_timestamp = time.time()
        self._last_cache_cleanup = time.time()
        self.skill_system = SkillSystem()
        self.profession_system = ProfessionSystem()

    def _load_or_new_player(self) -> Player:
        pj = self.storage.load_player()
        if pj:
            try:
                return Player.from_dict(pj)
            except Exception:
                logging.exception("Failed to load player data")
        p = Player()
        p.inventory = {"herb_common": 12, "iron": 3, "wood": 20, "stone": 15, "food": 10}
        self.storage.save_player(p.to_dict())
        return p

    def _ensure_world_pool(self):
        wids = self.storage.list_worlds()
        while len(wids) < CONFIG.get("WORLD_POOL_MIN", 6):
            w = WorldGenerator.generate(size_tier=random.choice(["small","medium","large"]))
            self.storage.save_world(w)
            wids = self.storage.list_worlds()

    def _get_cached_world(self, wid: str) -> Optional[World]:
        # تنظيف الكاش بشكل دوري
        now = time.time()
        if now - self._last_cache_cleanup > CONFIG["CACHE_CLEANUP_INTERVAL"]:
            self._clean_cache()
            self._last_cache_cleanup = now
            
        if wid in self._cached_worlds:
            self._cached_worlds[wid].last_access = now
            return self._cached_worlds[wid]
            
        w = self.storage.load_world(wid)
        if w:
            w.last_access = now
            self._cached_worlds[wid] = w
        return w

    def _clean_cache(self):
        """تنظيف الكاش من العوالم التي لم يتم استخدامها مؤخراً"""
        now = time.time()
        to_remove = [wid for wid, w in self._cached_worlds.items() 
                    if now - w.last_access > CONFIG["CACHE_CLEANUP_INTERVAL"]]
        for wid in to_remove:
            del self._cached_worlds[wid]

    def resolve_world(self, key: str) -> Optional[World]:
        if not key: return None
        
        # البحث في الكاش أولاً
        for wid, world in self._cached_worlds.items():
            if key == wid or key in normalize_ar_text(world.name):
                world.last_access = time.time()
                return world
                
        # البحث في قاعدة البيانات
        w = self.storage.load_world(key)
        if w: 
            w.last_access = time.time()
            self._cached_worlds[w.id] = w
            return w
            
        kn = normalize_ar_text(key)
        for wid in self.storage.list_worlds():
            wtemp = self._get_cached_world(wid)
            if not wtemp: continue
            if kn in wtemp.id or kn in normalize_ar_text(wtemp.name):
                return wtemp
        return None

    def find_creature_and_world_by_uid(self, uid: str) -> Optional[Tuple[World, CreatureInstance]]:
        for wid in self.storage.list_worlds():
            w = self._get_cached_world(wid)
            if not w: continue
            for cre in w.creatures:
                if cre.uid == uid:
                    return w, cre
        return None
        
    def list_worlds_brief(self) -> List[str]:
        out = []
        for wid in self.storage.list_worlds():
            w = self._get_cached_world(wid)
            if not w: continue
            
            # إضافة علامة إذا كان العالم مكتشفاً
            explored = "✓" if w.id in self.player.discovered_worlds else "✗"
            biome_name = BIOMES.get(w.biome, {"name": w.biome})["name"]
            
            settlement_info = ""
            if w.settlements:
                settlement_info = f" | مستوطنات: {len(w.settlements)}"
            
            out.append(f"{explored} {w.id} -> {w.name} | نمط={biome_name} | مخلوقات={len(w.creatures)}{settlement_info} | صعوبة={w.difficulty}")
        return out

    def describe_world(self, key: str) -> str:
        w = self.resolve_world(key)
        if not w: return "العالم غير موجود."
        
        # وضع علامة على العالم كمكتشف
        if w.id not in self.player.discovered_worlds:
            self.player.discovered_worlds.append(w.id)
            self.storage.save_player(self.player.to_dict())
        
        biome_data = BIOMES.get(w.biome, {})
        biome_name = biome_data.get("name", w.biome)
        
        settlement_info = ""
        if w.settlements:
            total_population = sum(settlement.population for settlement in w.settlements)
            settlement_info = f" | سكان: {total_population}"
        
        return (f"{colored_text(w.name, Colors.CYAN)}: {w.description}. "
                f"حجم={w.size_cubes}, عناصر={w.total_elements()}, "
                f"مخلوقات={len(w.creatures)}{settlement_info}, سعة={w.carrying_capacity}.")

    def list_creatures(self, key: str) -> str:
        w = self.resolve_world(key)
        if not w: return "العالم غير موجود."
        if not w.creatures: return f"لا توجد مخلوقات في {w.name}."
        
        lines = [f"مخلوقات في {w.name}:"]
        for c in w.creatures:
            spec = CREATURES.get(c.spec_id, {"name": c.spec_id})
            health_percent = c.energy / spec.get("energy_max", 1) * 100
            health_color = Colors.GREEN if health_percent > 70 else Colors.YELLOW if health_percent > 30 else Colors.RED
            
            summon_info = ""
            if c.summoned:
                time_left = c.summon_expires - time.time()
                if time_left > 0:
                    summon_info = f" | مستدعى: {int(time_left)}s"
                else:
                    summon_info = " | مستدعى: منتهي"
            
            lines.append(
                f"[{c.uid}] {spec['name']} ({c.spec_id}) | "
                f"{colored_text(f'طاقة={c.energy:.1f}', health_color)} | عمر={c.age}{summon_info}"
            )
        return "\n".join(lines)

    def gather(self, key: str, count: int = 1) -> str:
        with self.lock:
            w = self.resolve_world(key)
            if not w: return "العالم غير موجود."
            if w.total_elements() == 0: return "لا توجد عناصر في هذا العالم."
            
            gathered = {}
            for _ in range(min(count, w.total_elements())):
                choices = list(w.elements.items())
                if not choices: break
                ids, weights = zip(*choices)
                pick = random.choices(ids, weights=weights, k=1)[0]
                gathered[pick] = gathered.get(pick,0) + 1
                w.elements[pick] -= 1
                if w.elements[pick] <= 0: del w.elements[pick]
                
                # XP مكافأة تعتمد على ندرة المورد
                rarity = BLOCKS.get(pick,{}).get("rarity", 0.2)
                gain = (1.0 / (rarity + 0.05)) * 0.5
                self.player.gain_xp(gain)
                
                # خبرة مهارة الجمع
                xp_gained = self.skill_system.gain_skill_xp("gathering", "gather", 1.0)
                level_up_msg = self.player.gain_skill_xp("gathering", xp_gained)
                
            for k,v in gathered.items():
                self.player.inventory[k] = self.player.inventory.get(k,0) + v
                
            self.storage.save_world(w)
            self.storage.save_player(self.player.to_dict())
            
            if not gathered: return "لم تجمع شيئًا."
            parts = [f"{v}× {BLOCKS.get(k,{'name':k})['name']}" for k,v in gathered.items()]
            
            result = "جمعت: " + "، ".join(parts) + "."
            if level_up_msg:
                result += " " + level_up_msg
            return result

    def ingest(self, key: str, requested: Optional[int] = None) -> str:
        with self.lock:
            w = self.resolve_world(key)
            if not w: return "العالم غير موجود."
            total = w.total_elements()
            if total == 0: return "لا توجد عناصر للابتلاع."
            
            # تطبيق تأثير مهارة الابتلاع
            ingestion_skill = self.player.skills.get("ingestion_efficiency", 1)
            skill_effect = self.skill_system.get_skill_effect("ingestion_efficiency", ingestion_skill)
            
            capacity_bonus = skill_effect.get("capacity_bonus", 0)
            efficiency = skill_effect.get("efficiency", 1.0)
            
            base_capacity = 3 * self.player.level
            efficiency_bonus = base_capacity * (0.1 * (ingestion_skill - 1))
            capacity_boost = self.player.effect_system.get_effect_value("ingestion_capacity")
            
            per = max(1, int(base_capacity + efficiency_bonus + capacity_boost + capacity_bonus))
            per = int(per * efficiency)
            
            take = per if requested is None else min(per, requested)
            take = min(take, total)
            
            # ترتيب العناصر حسب الندرة (الأندر أولاً)
            pool = sorted(w.elements.items(), key=lambda kv: get_rarity(kv[0]))
            
            ingested = {}
            rem = take
            for bid,cnt in pool:
                if rem <= 0: break
                t = min(cnt, rem)
                ingested[bid] = t
                w.elements[bid] -= t
                if w.elements[bid] <= 0: del w.elements[bid]
                rem -= t
                
            snap_key = make_key(w.id)
            snap = {
                "elements": ingested, 
                "source": w.id, 
                "time": time.time(), 
                "size": sum(ingested.values()), 
                "seed": w.seed
            }
            
            self.storage.save_snapshot(snap_key, w.id, snap)
            
            for bid,cnt in ingested.items():
                self.player.inner.elements[bid] = self.player.inner.elements.get(bid,0) + cnt
                
            self.player.inner.size_cubes = sum(self.player.inner.elements.values())
            self.player.inner.ingested_keys.append(snap_key)
            
            # توليد الجوهر من العناصر المبتلعة
            essence_gained = {}
            for bid,cnt in ingested.items():
                for _ in range(cnt):
                    prob = min(0.6, 0.08 + (1 - BLOCKS.get(bid,{"rarity":1})["rarity"]) * 0.25 + self.player.level * 0.01)
                    if random.random() < prob:
                        ess = f"essence_{bid}"
                        essence_gained[ess] = essence_gained.get(ess, 0) + 1
                        self.player.inventory[ess] = self.player.inventory.get(ess,0) + 1
                        
            self.player.gain_xp(take * 0.9)
            
            # خبرة مهارة الابتلاع
            xp_gained = self.skill_system.gain_skill_xp("ingestion_efficiency", "ingest", take)
            level_up_msg = self.player.gain_skill_xp("ingestion_efficiency", xp_gained)
            
            # حساب تكلفة Qi مع مراعاة المهارات
            qi_skill = self.player.skills.get("qi_mastery", 1)
            qi_skill_effect = self.skill_system.get_skill_effect("qi_mastery", qi_skill)
            qi_cost_modifier = 1 - qi_skill_effect.get("qi_cost_reduction", 0.0)
            
            qi_cost = take * (0.3 * w.difficulty) * qi_cost_modifier
            self.player.qi = max(0, self.player.qi - qi_cost)
            
            # تحديث إحصائيات الابتلاع
            self.player.ingested_worlds_count += 1
            if self.player.achievements.check_achievement("first_ingestion"):
                self.player.gain_xp(100)
            if self.player.achievements.check_achievement("master_ingester", 1):
                self.player.gain_xp(200)
            
            # إذا تم ابتلاع العالم بالكامل، حذفه وإنشاء عالم جديد
            if w.total_elements() == 0 and len(w.creatures) == 0:
                self.storage.delete_world(w.id)
                if w.id in self._cached_worlds:
                    del self._cached_worlds[w.id]
                self._ensure_world_pool()
            else:
                self.storage.save_world(w)
                
            self.storage.save_player(self.player.to_dict())
            
            parts = [f"{v}× {BLOCKS.get(k,{'name':k})['name']}" for k,v in ingested.items()]
            result = f"ابتلعت {sum(ingested.values())} مكعبات: " + "، ".join(parts) + f". مفتاح: {snap_key}."
            
            if essence_gained:
                essence_parts = [f"{v}× {k}" for k,v in essence_gained.items()]
                result += f" حصلت على جوهر: {', '.join(essence_parts)}."
            
            if level_up_msg:
                result += " " + level_up_msg
                
            return result

    def ingest_creature(self, key_or_uid: str, creature_uid: Optional[str] = None) -> str:
        with self.lock:
            w, cre = None, None
            if creature_uid is None: # Ingest using only UID
                result = self.find_creature_and_world_by_uid(key_or_uid)
                if result:
                    w, cre = result
                else:
                    return "المخلوق غير موجود في أي عالم."
            else: # Ingest using world key and UID
                w = self.resolve_world(key_or_uid)
                if not w: return "العالم غير موجود."
                cre = next((c for c in w.creatures if c.uid == creature_uid), None)
                if not cre:
                    # Fallback to search all worlds if not found in the specified one
                    result = self.find_creature_and_world_by_uid(creature_uid)
                    if result:
                        w, cre = result
                    else:
                        return "المخلوق غير موجود في هذا العالم."

            spec = CREATURES.get(cre.spec_id)
            if not spec: return "نوع المخلوق غير معروف."
            if any(c.uid == cre.uid for c in self.player.inner.creatures):
                return "لقد ابتلعت هذا المخلوق بالفعل."
                
            cost_cubes = max(1, int(spec["energy_max"] * 0.04))
            if self.player.inner.size_cubes + cost_cubes > self.player.inner.capacity_cubes:
                return "سعة العالم الداخلي غير كافية."
                
            inner_cre = CreatureInstance(cre.uid, cre.spec_id, min(cre.energy, spec["energy_max"]*0.6), cre.age)
            self.player.inner.creatures.append(inner_cre)
            self.player.inner.size_cubes += cost_cubes
            
            if cre in w.creatures:
                w.creatures.remove(cre)
            
            # فرصة الحصول على جوهر المخلوق
            essence_chance = 0.3 + (self.player.skills.get("qi_mastery", 1) * 0.1)
            if random.random() < essence_chance:
                essence_id = f"essence_{cre.spec_id}"
                self.player.inventory[essence_id] = self.player.inventory.get(essence_id, 0) + 1
                essence_msg = " وحصلت على جوهر المخلوق!"
            else:
                essence_msg = ""
            
            self.player.gain_xp(30.0)
            self.storage.save_world(w)
            self.storage.save_player(self.player.to_dict())
            return f"نجحت في ابتلاع {spec['name']} ({cre.uid}) وأصبح داخل عالمك.{essence_msg}"

    def energy_attack(self, key_or_uid: str, creature_uid: Optional[str] = None, energy_cost: float = 25.0) -> str:
        with self.lock:
            # تطبيق تأثير مهارة القتال
            combat_skill = self.player.skills.get("combat", 1)
            skill_effect = self.skill_system.get_skill_effect("combat", combat_skill)
            attack_bonus = skill_effect.get("attack_bonus", 1.0)
            
            qi_cost_modifier = 1 - (0.05 * (self.player.skills.get("qi_mastery", 1) - 1))
            actual_cost = energy_cost * qi_cost_modifier
            if self.player.qi < actual_cost:
                return "لا تملك Qi كافية."

            w, cre = None, None
            if creature_uid is None: # Attack using only UID
                result = self.find_creature_and_world_by_uid(key_or_uid)
                if result:
                    w, cre = result
                else:
                    return "المخلوق غير موجود في أي عالم."
            else: # Attack using world key and UID
                w = self.resolve_world(key_or_uid)
                if not w: return "العالم غير موجود."
                cre = next((c for c in w.creatures if c.uid == creature_uid), None)
                if not cre:
                    # Fallback to search all worlds if not found in the specified one
                    result = self.find_creature_and_world_by_uid(creature_uid)
                    if result:
                        w, cre = result
                    else:
                        return "المخلوق غير موجود في هذا العالم."

            spec = CREATURES.get(cre.spec_id)
            if not spec: return "نوع المخلوق غير معروف."
            
            # حساب الضرر مع مراعاة مهارات القتال
            damage = (self.player.level * 2.0 * attack_bonus) + (energy_cost * 0.8)
            
            cre.energy -= damage
            self.player.qi -= actual_cost
            
            out = f"هاجمت طاقيًا {spec['name']} بـ{damage:.1f} ضرر. طاقة المخلوق الآن {cre.energy:.1f}."
            
            if cre.energy <= 0:
                if cre in w.creatures:
                    w.creatures.remove(cre)
                
                # إضافة موارد واقعية من الجثة
                w.elements["corpse"] = w.elements.get("corpse", 0) + 1
                w.elements["bones"] = w.elements.get("bones", 0) + 1
                essence = f"essence_{cre.spec_id}"
                self.player.inventory[essence] = self.player.inventory.get(essence,0) + 1
                
                self.player.gain_xp(25.0 * (1.0/(spec.get("rarity",0.1)+0.01)))
                out += f" {spec['name']} هُزم وحصلت على {essence} وعظام وجثة."
                
            # خبرة مهارة القتال
            xp_gained = self.skill_system.gain_skill_xp("combat", "fight", damage)
            level_up_msg = self.player.gain_skill_xp("combat", xp_gained)
            if level_up_msg:
                out += " " + level_up_msg
                
            self.storage.save_world(w)
            self.storage.save_player(self.player.to_dict())
            return out
    
    def plant(self, item_id: str, count: int = 1) -> str:
        with self.lock:
            if self.player.inventory.get(item_id,0) < count:
                return "لا تملك هذه الكمية في المخزن."
            if self.player.inner.size_cubes + count > self.player.inner.capacity_cubes:
                return "سعة العالم الداخلي لا تسمح بهذه العملية."
            if item_id not in BLOCKS:
                return "هذا العنصر غير صالح للزرع."
            if BLOCKS[item_id]["category"] not in ("plant","ground","fungus"):
                return "المورد ليس نباتي/أرضي ولا يمكن زرعه."
                
            # التحقق من متطلبات الزراعة
            if item_id in ELEMENT_RELATIONSHIPS:
                requirements = ELEMENT_RELATIONSHIPS[item_id].get("requires", [])
                if not all(req in self.player.inner.elements for req in requirements):
                    return f"تحتاج إلى {', '.join(requirements)} لزرع {item_id}"
                
            self.player.inventory[item_id] -= count
            if self.player.inventory[item_id] <= 0: 
                del self.player.inventory[item_id]
                
            self.player.inner.elements[item_id] = self.player.inner.elements.get(item_id,0) + count
            self.player.inner.size_cubes += count
            
            # خبرة مهارة الزراعة
            xp_gained = self.skill_system.gain_skill_xp("cultivation", "plant", count)
            level_up_msg = self.player.gain_skill_xp("cultivation", xp_gained)
            
            self.storage.save_player(self.player.to_dict())
            
            result = f"زرعت {count}× {BLOCKS[item_id]['name']} داخل عالمك الداخلي."
            if level_up_msg:
                result += " " + level_up_msg
            return result

    def harvest(self, item_id: str, count: int = 1) -> str:
        with self.lock:
            if self.player.inner.elements.get(item_id,0) < count:
                return "لا توجد كمية كافية في الداخل للحصد."
                
            self.player.inner.elements[item_id] -= count
            if self.player.inner.elements[item_id] <= 0: 
                del self.player.inner.elements[item_id]
                
            self.player.inner.size_cubes = max(0, self.player.inner.size_cubes - count)
            self.player.inventory[item_id] = self.player.inventory.get(item_id,0) + count
            
            # إنتاج البذور من النباتات
            if item_id in ELEMENT_RELATIONSHIPS and "produces" in ELEMENT_RELATIONSHIPS[item_id]:
                for product in ELEMENT_RELATIONSHIPS[item_id]["produces"]:
                    if random.random() < 0.3:  # 30% chance for seeds
                        self.player.inventory[product] = self.player.inventory.get(product, 0) + 1
            
            # خبرة مهارة الزراعة
            xp_gained = self.skill_system.gain_skill_xp("cultivation", "harvest", count)
            level_up_msg = self.player.gain_skill_xp("cultivation", xp_gained)
            
            self.storage.save_player(self.player.to_dict())
            
            result = f"حصدت {count}× {BLOCKS.get(item_id,{'name':item_id})['name']} وأضيفت إلى المخزن."
            if level_up_msg:
                result += " " + level_up_msg
            return result

    def mount(self, key: str, point: str) -> str:
        with self.lock:
            w = self.resolve_world(key)
            if not w: return "العالم غير موجود."
            self.player.inner.mounts[point] = w.id
            self.storage.save_player(self.player.to_dict())
            return f"ركبت {w.name} في '{point}'."

    def unmount(self, point: str) -> str:
        with self.lock:
            if point not in self.player.inner.mounts:
                return "نقطة التركيب غير موجودة."
            wid = self.player.inner.mounts.pop(point)
            self.storage.save_player(self.player.to_dict())
            return f"ألغيت تركيب {wid} من '{point}'."

    def list_snapshots(self) -> str:
        snaps = self.storage.list_snapshots()
        if not snaps: return "لا توجد لقطات محفوظة."
        out = [f"اللقطات ({len(snaps)}):"]
        for s in snaps[:100]:
            out.append(f"- {s['key']} من {s['source_world']} @ {time.ctime(s['timestamp'])}")
        return "\n".join(out)

    def show_snapshot(self, key: str) -> str:
        sn = self.storage.load_snapshot(key)
        if not sn: return "لا توجد لقطة بهذا المفتاح."
        return json.dumps(sn["data"], ensure_ascii=False, indent=2)

    def delete_snapshot(self, key: str) -> str:
        self.storage.delete_snapshot(key)
        return f"تم حذف اللقطة {key}."

    def cleanup_snapshots(self, keep_n: int = CONFIG["MAX_SNAPSHOTS_PER_WORLD"]) -> str:
        """تنظيف اللقطات والاحتفاظ بأحدث N لقطة لكل عالم"""
        deleted_count = self.storage.cleanup_snapshots_keep_recent(keep_n)
        return f"تم حذف {deleted_count} لقطة، والاحتفاظ بـ{keep_n} لقطة لكل عالم."

    def export_state(self, filename: str) -> str:
        fname = self.storage.export_all(filename)
        return f"تم التصدير إلى {fname}"

    def save_player(self) -> str:
        self.storage.save_player(self.player.to_dict())
        return "تم الحفظ."

    def develop_skill(self, skill_name: str) -> str:
        skill_map = {
            "ابتلاع": "ingestion_efficiency", 
            "طاقة": "qi_mastery", 
            "زراعة": "cultivation",
            "قتال": "combat",
            "بناء": "building",
            "تجارة": "trading",
            "جمع": "gathering",
            "صنعة": "crafting"
        }
        norm_skill = normalize_ar_text(skill_name)
        
        target_skill = None
        for k, v in skill_map.items():
            if norm_skill == normalize_ar_text(k):
                target_skill = v
                break
        
        if not target_skill or target_skill not in self.player.skills:
            return f"مهارة غير معروفة. المهارات المتاحة: {', '.join(skill_map.keys())}"

        current_level = self.player.skills[target_skill]
        cost = 50 * (1.8 ** current_level)
        
        if self.player.xp < cost:
            return f"تحتاج إلى {cost:.1f} XP لتطوير هذه المهارة (لديك {self.player.xp:.1f} XP)."
            
        self.player.xp -= cost
        self.player.skills[target_skill] += 1
        self.storage.save_player(self.player.to_dict())
        
        return f"تم تطوير مهارة '{skill_name}' إلى المستوى {current_level + 1}!"

    def craft_item(self, recipe_id: str) -> str:
        recipe = RECIPES.get(recipe_id)
        if not recipe:
            return "وصفة غير موجودة."

        for item, required in recipe["ingredients"].items():
            if self.player.inventory.get(item, 0) < required:
                return f"لا تملك مكونات كافية. تحتاج إلى {required} من {item}."
        
        for item, required in recipe["ingredients"].items():
            self.player.inventory[item] -= required
            if self.player.inventory[item] == 0:
                del self.player.inventory[item]
        
        for item, amount in recipe["output"].items():
            self.player.inventory[item] = self.player.inventory.get(item, 0) + amount
            
        # خبرة مهارة البناء
        xp_gained = self.skill_system.gain_skill_xp("crafting", "craft", 1.0)
        level_up_msg = self.player.gain_skill_xp("crafting", xp_gained)
            
        # تحديث إحصائيات الصناعة
        self.player.crafted_items_count += 1
        if self.player.achievements.check_achievement("craft_master", 1):
            self.player.gain_xp(150)
            
        self.storage.save_player(self.player.to_dict())
        
        result = f"نجحت في صناعة: {recipe['name']}."
        if level_up_msg:
            result += " " + level_up_msg
        return result

    def build_structure(self, structure_id: str) -> str:
        if structure_id not in BUILDINGS:
            return "هذا الهيكل غير معروف."
            
        building_data = BUILDINGS[structure_id]
        cost = building_data.get("cost", {})
        
        # التحقق من الموارد
        for resource, amount in cost.items():
            if self.player.inventory.get(resource, 0) < amount:
                return f"تحتاج إلى {amount} من {resource} لبناء هذا الهيكل."
        
        # استهلاك الموارد
        for resource, amount in cost.items():
            self.player.inventory[resource] -= amount
            if self.player.inventory[resource] <= 0:
                del self.player.inventory[resource]
        
        # بناء الهيكل
        self.player.inner.buildings.buildings[structure_id] = \
            self.player.inner.buildings.buildings.get(structure_id, 0) + 1
        
        # خبرة البناء
        xp_gained = self.skill_system.gain_skill_xp("building", "build", 1.0)
        level_up_msg = self.player.gain_skill_xp("building", xp_gained)
        
        # تحقيق إنجاز إذا كان أول بناء
        if self.player.achievements.check_achievement("settlement_founder"):
            self.player.gain_xp(200)
            
        self.storage.save_player(self.player.to_dict())
        
        result = f"تم بناء {building_data['name']} بنجاح!"
        if level_up_msg:
            result += " " + level_up_msg
        return result

    def create_settlement(self, name: str) -> str:
        if any(s.name == name for s in self.player.inner.settlements):
            return "هناك مستوطنة بنفس الاسم بالفعل."
            
        # تكلفة إنشاء مستوطنة
        settlement_cost = {"wood": 50, "stone": 30, "herb_common": 100}
        for resource, amount in settlement_cost.items():
            if self.player.inventory.get(resource, 0) < amount:
                return f"تحتاج إلى {amount} من {resource} لإنشاء مستوطنة."
        
        # استهلاك الموارد
        for resource, amount in settlement_cost.items():
            self.player.inventory[resource] -= amount
            if self.player.inventory[resource] <= 0:
                del self.player.inventory[resource]
        
        # إنشاء المستوطنة
        new_settlement = HumanSettlement(name, 10)
        self.player.inner.settlements.append(new_settlement)
        
        # تحديث إنجاز حاكم المستوطنات
        if self.player.achievements.check_achievement("settlement_ruler", 1):
            self.player.gain_xp(300)
        
        self.storage.save_player(self.player.to_dict())
        return f"تم إنشاء مستوطنة {name} بنجاح!"

    def list_settlements(self) -> str:
        """عرض جميع المستوطنات"""
        output = ["🏘️ مستوطناتك الداخلية:"]
        settlements = self.player.inner.settlements
        if settlements:
            for i, settlement in enumerate(settlements):
                output.append(f"{i+1}. {settlement.name} - {settlement.population} سكان")
        else:
            output.append("لا توجد مستوطنات في عالمك الداخلي")
            
        # المستوطنات في العوالم الخارجية
        world_settlements = []
        for wid in self.storage.list_worlds():
            w = self._get_cached_world(wid)
            if w and w.settlements:
                for settlement in w.settlements:
                    world_settlements.append(f"{settlement.name} في {w.name}")
        
        if world_settlements:
            output.append("\n🏘️ المستوطنات في العوالم الخارجية:")
            for settlement in world_settlements:
                output.append(f"- {settlement}")
        
        return "\n".join(output)

    def show_settlement(self, settlement_name: str) -> str:
        """عرض معلومات مستوطنة"""
        # البحث في المستوطنات الداخلية
        for settlement in self.player.inner.settlements:
            if settlement.name == settlement_name:
                return settlement.get_info()
        
        # البحث في المستوطنات الخارجية
        for wid in self.storage.list_worlds():
            w = self._get_cached_world(wid)
            if w:
                for settlement in w.settlements:
                    if settlement.name == settlement_name:
                        info = settlement.get_info()
                        info += f"\n📍 الموقع: {w.name}"
                        return info
        
        return "المستوطنة غير موجودة"

    def collect_settlement_resources(self, settlement_name: str, resource: str = None, amount: int = None) -> str:
        """جمع موارد من مستوطنة"""
        # البحث عن المستوطنة
        settlement = None
        # البحث في الداخل أولاً
        for s in self.player.inner.settlements:
            if s.name == settlement_name:
                settlement = s
                break
        
        # البحث في الخارج إذا لم تُوجد في الداخل
        if not settlement:
            for wid in self.storage.list_worlds():
                w = self._get_cached_world(wid)
                if w:
                    for s in w.settlements:
                        if s.name == settlement_name:
                            settlement = s
                            break
                if settlement:
                    break
        
        if settlement:
            collected = settlement.collect_resources(resource, amount)
            if collected:
                # إضافة الموارد إلى مخزون اللاعب
                for res, amt in collected.items():
                    self.player.inventory[res] = self.player.inventory.get(res, 0) + amt
                
                output = [f"جمعت من {settlement_name}:"]
                for res, amt in collected.items():
                    res_name = BLOCKS.get(res, {}).get('name', res)
                    output.append(f"- {res_name}: {amt}")
                
                self.storage.save_player(self.player.to_dict())
                return "\n".join(output)
            else:
                return "لم يتم جمع أي موارد"
        else:
            return "المستوطنة غير موجودة"

    def build_in_settlement(self, settlement_name: str, building_id: str) -> str:
        """بناء مبنى في مستوطنة"""
        settlement = None
        # البحث في المستوطنات الداخلية
        for s in self.player.inner.settlements:
            if s.name == settlement_name:
                settlement = s
                break
        
        if settlement and building_id in BUILDINGS:
            cost = BUILDINGS[building_id].get("cost", {})
            
            # التحقق من الموارد
            can_build = True
            for res, amt in cost.items():
                if settlement.resources.get(res, 0) < amt:
                    return f"تحتاج إلى {amt} من {res}"
                    can_build = False
                    break
            
            if can_build:
                # خصم الموارد
                for res, amt in cost.items():
                    settlement.resources[res] -= amt
                
                # بناء المبنى
                settlement.buildings[building_id] = settlement.buildings.get(building_id, 0) + 1
                building_name = BUILDINGS[building_id].get("name", building_id)
                
                self.storage.save_player(self.player.to_dict())
                return f"تم بناء {building_name} في {settlement_name}"
        else:
            return "المستوطنة أو المبنى غير موجود"

    def assign_profession(self, settlement_name: str, profession: str, count: int) -> str:
        """تعيين مهنة للمستوطنين"""
        settlement = None
        # البحث في المستوطنات الداخلية
        for s in self.player.inner.settlements:
            if s.name == settlement_name:
                settlement = s
                break
        
        if settlement:
            result = settlement.assign_profession(profession, count)
            self.storage.save_player(self.player.to_dict())
            return result
        else:
            return "المستوطنة غير موجودة"

    def ingest_settlement_creatures(self, settlement_name: str, max_creatures: int = None) -> str:
        """ابتلاع مخلوقات من مستوطنة"""
        settlement = None
        world = None
        
        # البحث عن المستوطنة
        for wid in self.storage.list_worlds():
            w = self._get_cached_world(wid)
            if w and w.settlements:
                for s in w.settlements:
                    if s.name == settlement_name:
                        settlement = s
                        world = w
                        break
            if settlement:
                break
        
        if not settlement:
            return "المستوطنة غير موجودة في أي عالم"
        
        # المخلوقات في عالم المستوطنة
        creatures_to_ingest = []
        for creature in world.creatures:
            # يمكن ابتلاع المخلوقات القريبة من المستوطنة
            if random.random() < 0.7:  # 70% chance
                creatures_to_ingest.append(creature)
        
        if not creatures_to_ingest:
            return "لا توجد مخلوقات قريبة من المستوطنة"
        
        if max_creatures:
            creatures_to_ingest = creatures_to_ingest[:max_creatures]
        
        ingested_count = 0
        for creature in creatures_to_ingest:
            if creature in world.creatures:
                spec = CREATURES.get(creature.spec_id)
                if spec:
                    cost_cubes = max(1, int(spec["energy_max"] * 0.04))
                    if self.player.inner.size_cubes + cost_cubes <= self.player.inner.capacity_cubes:
                        inner_cre = CreatureInstance(
                            creature.uid, creature.spec_id, 
                            min(creature.energy, spec["energy_max"] * 0.6), 
                            creature.age
                        )
                        self.player.inner.creatures.append(inner_cre)
                        self.player.inner.size_cubes += cost_cubes
                        world.creatures.remove(creature)
                        ingested_count += 1
        
        if ingested_count > 0:
            self.storage.save_world(world)
            self.storage.save_player(self.player.to_dict())
            return f"ابتلعت {ingested_count} مخلوقات من حول مستوطنة {settlement_name}"
        else:
            return "لم تتمكن من ابتلاع أي مخلوقات"

    def trade_item(self, action: str, item_id: str, quantity: int = 1) -> str:
        """تجارة عنصر مع السوق العالمي"""
        if action not in ["شراء", "بيع"]:
            return "استخدم 'شراء' أو 'بيع'"
        
        is_buying = (action == "شراء")
        
        if is_buying:
            # الشراء: التحقق من العملات
            success, total_cost = self.player.economy.execute_trade(
                item_id, quantity, is_buying, self.player.skills.get("trading", 1)
            )
            if not success:
                return f"لا تملك عملات كافية. التكلفة: {total_cost}"
            
            # إضافة العنصر للمخزون
            self.player.inventory[item_id] = self.player.inventory.get(item_id, 0) + quantity
            result = f"اشتريت {quantity} من {item_id} بسعر {total_cost} عملة"
        else:
            # البيع: التحقق من المخزون
            if self.player.inventory.get(item_id, 0) < quantity:
                return f"لا تملك {quantity} من {item_id}"
            
            success, total_income = self.player.economy.execute_trade(
                item_id, quantity, is_buying, self.player.skills.get("trading", 1)
            )
            
            # إزالة العنصر من المخزون
            self.player.inventory[item_id] -= quantity
            if self.player.inventory[item_id] <= 0:
                del self.player.inventory[item_id]
            
            result = f"بعت {quantity} من {item_id} بسعر {total_income} عملة"
        
        # خبرة مهارة التجارة
        xp_gained = self.skill_system.gain_skill_xp("trading", "trade", quantity)
        level_up_msg = self.player.gain_skill_xp("trading", xp_gained)
        
        # تحديث إحصائيات التجارة
        self.player.achievements.check_achievement("trade_master", 1)
        
        self.storage.save_player(self.player.to_dict())
        
        if level_up_msg:
            result += " " + level_up_msg
        return result

    def show_skills(self) -> str:
        """عرض المهارات بشكل مفصل"""
        output = ["🎯 مهاراتك الحالية:"]
        
        for skill_name, level in self.player.skills.items():
            skill_effect = self.skill_system.get_skill_effect(skill_name, level)
            xp_progress = self.player.skill_experience.get(skill_name, 0)
            xp_needed = level * 100
            
            skill_display_name = self.player.get_skill_name(skill_name)
            output.append(
                f"• {skill_display_name}: المستوى {level} "
                f"({xp_progress:.1f}/{xp_needed} XP)"
            )
            
            # عرض تأثيرات المهارة
            if skill_effect:
                for effect, value in skill_effect.items():
                    effect_name = self._get_effect_name(effect)
                    output.append(f"  ↳ {effect_name}: {value}")
        
        return "\n".join(output)

    def _get_effect_name(self, effect_key: str) -> str:
        """ترجمة مفاتيح التأثيرات إلى أسماء عربية"""
        effect_names = {
            "capacity_bonus": "زيادة السعة",
            "efficiency": "الكفاءة",
            "qi_cost_reduction": "تقليل تكلفة الطاقة",
            "generation_boost": "تعزيز التوليد",
            "growth_boost": "تعزيز النمو",
            "yield_bonus": "زيادة المحصول",
            "attack_bonus": "تعزيز الهجوم",
            "defense_bonus": "تعزيز الدفاع",
            "build_speed": "سرعة البناء",
            "cost_reduction": "تقليل التكلفة",
            "price_advantage": "ميزة السعر",
            "bargaining": "المساومة"
        }
        return effect_names.get(effect_key, effect_key)

    def show_professions(self) -> str:
        """عرض المهن المتاحة"""
        output = ["🏭 المهن المتاحة:"]
        
        for profession_id, profession_data in PROFESSIONS.items():
            output.append(f"\n{profession_data.get('name', profession_id)}:")
            output.append(f"  المهارات المطلوبة: {', '.join(profession_data.get('required_skills', {}).keys())}")
            output.append(f"  الإنتاج: {', '.join([f'{k}({v})' for k, v in profession_data.get('production', {}).items()])}")
        
        return "\n".join(output)

    def check_currency(self) -> str:
        """عرض عملات اللاعب"""
        output = ["💰 عملاتك:"]
        
        for currency_id, amount in self.player.economy.player_wealth.items():
            currency_data = CURRENCY.get(currency_id, {})
            currency_name = currency_data.get('name', currency_id)
            output.append(f"{currency_name}: {amount}")
        
        total_value = sum(amount * CURRENCY.get(currency_id, {}).get('value', 1) 
                         for currency_id, amount in self.player.economy.player_wealth.items())
        output.append(f"\nالقيمة الإجمالية: {total_value} عملة قياسية")
        
        return "\n".join(output)

    def check_production(self, settlement_name: str) -> str:
        """عرض إنتاج المستوطنة"""
        settlement = None
        # البحث في المستوطنات الداخلية
        for s in self.player.inner.settlements:
            if s.name == settlement_name:
                settlement = s
                break
        
        if not settlement:
            return "المستوطنة غير موجودة."
        
        production = settlement.get_production()
        output = [f"🏭 إنتاج مستوطنة {settlement_name}:"]
        
        if production:
            for item, amount in production.items():
                item_name = BLOCKS.get(item, {}).get('name', item) or item
                output.append(f"  {item_name}: {amount}")
        else:
            output.append("لا يوجد إنتاج حالياً.")
        
        output.append(f"\nالمهن المعينة: {', '.join([f'{k}({v})' for k, v in settlement.professions.items()])}")
        output.append(f"إجمالي السكان: {settlement.population}")
        
        return "\n".join(output)

    def train_skill(self, skill_name: str, hours: int) -> str:
        """تدريب مهارة معينة"""
        if skill_name not in self.player.skills:
            return "المهارة غير موجودة."
        
        if hours <= 0:
            return "عدد الساعات يجب أن يكون موجباً."
        
        # حساب خبرة التدريب
        xp_gained = hours * 20.0  # 2 XP لكل ساعة تدريب
        level_up_msg = self.player.gain_skill_xp(skill_name, xp_gained)
        
        # استهلاك الطاقة أثناء التدريب
        energy_cost = hours * 0.2
        self.player.qi = max(0, self.player.qi - energy_cost)
        
        result = f"تدربت على {self.player.get_skill_name(skill_name)} لمدة {hours} ساعات."
        if level_up_msg:
            result += " " + level_up_msg
        
        self.storage.save_player(self.player.to_dict())
        return result

    def apply_element_relationships(self, world: World) -> List[str]:
        """تطبيق العلاقات بين العناصر في العالم"""
        messages = []
        
        # إنشاء نسخة من العناصر لتجنب تغيير القاموس أثناء التكرار
        elements_copy = world.elements.copy()
        
        for element_id, count in elements_copy.items():
            if element_id in ELEMENT_RELATIONSHIPS:
                relations = ELEMENT_RELATIONSHIPS[element_id]
                
                # التحقق من المتطلبات للنمو
                if "requires" in relations:
                    has_requirements = all(req in world.elements for req in relations["requires"])
                    if has_requirements and "growth_rate" in relations:
                        growth = relations["growth_rate"] * world.regen_rate_scalar
                        new_count = int(count * (1 + growth))
                        if new_count > count:
                            world.elements[element_id] = new_count
                            messages.append(f"{element_id} نما من {count} إلى {new_count}")
                
                # الإنتاج التلقائي
                if "produces" in relations and random.random() < 0.1:
                    for product in relations["produces"]:
                        world.elements[product] = world.elements.get(product, 0) + 1
                        messages.append(f"{element_id} أنتج {product}")
                
                # التحلل
                if "decays_to" in relations and random.random() < 0.05:
                    decay_product = relations["decays_to"]
                    decay_amount = max(1, int(count * 0.1))
                    if world.elements.get(element_id, 0) >= decay_amount:
                        world.elements[element_id] -= decay_amount
                        world.elements[decay_product] = world.elements.get(decay_product, 0) + decay_amount
                        if world.elements[element_id] <= 0:
                            del world.elements[element_id]
                        messages.append(f"{decay_amount} من {element_id} تحللت إلى {decay_product}")
        
        return messages

    def handle_predation_and_resources(self, world: World) -> List[str]:
        """معالجة الافتراس وإضافة الموارد من الجثث"""
        messages = []
        creatures_to_remove = []
        
        for predator in world.creatures:
            spec = CREATURES.get(predator.spec_id)
            if not spec or spec["diet"] != "carnivore":
                continue
                
            if predator.spec_id in PREDATION:
                potential_prey = [c for c in world.creatures if c.spec_id in PREDATION[predator.spec_id] and c.uid != predator.uid]
                if potential_prey and random.random() < 0.15:
                    prey = random.choice(potential_prey)
                    damage = spec["attack"] * random.uniform(0.5, 1.4)
                    prey.energy -= damage
                    
                    # نقل الطاقة
                    energy_gain = damage * 0.6
                    predator.energy = min(spec["energy_max"], predator.energy + energy_gain)
                    
                    messages.append(f"{spec['name']} هاجم {CREATURES[prey.spec_id]['name']} وتسبب في {damage:.1f} ضرر")
                    
                    if prey.energy <= 0:
                        # إضافة موارد من الجثة
                        world.elements["corpse"] = world.elements.get("corpse", 0) + 1
                        world.elements["bones"] = world.elements.get("bones", 0) + 1
                        world.elements[f"essence_{prey.spec_id}"] = world.elements.get(f"essence_{prey.spec_id}", 0) + 1
                        
                        creatures_to_remove.append(prey)
                        messages.append(f"{CREATURES[prey.spec_id]['name']} مات وأضاف موارد للعالم")
        
        # إزالة المخلوقات الميتة
        for creature in creatures_to_remove:
            if creature in world.creatures:
                world.creatures.remove(creature)
        
        return messages

    def decomposition_system(self, world: World) -> List[str]:
        """نظام التحلل للجثث والموارد"""
        messages = []
        
        # تحلل الجثث
        if "corpse" in world.elements and world.elements["corpse"] > 0:
            decay_rate = 0.1 * world.regen_rate_scalar
            decayed = int(world.elements["corpse"] * decay_rate)
            if decayed > 0:
                world.elements["corpse"] -= decayed
                world.elements["dirt"] = world.elements.get("dirt", 0) + int(decayed * 0.7)
                world.elements["bones"] = world.elements.get("bones", 0) + int(decayed * 0.3)
                
                if world.elements["corpse"] <= 0:
                    del world.elements["corpse"]
                
                messages.append(f"{decayed} جثة تحللت إلى تربة وعظام")
        
        # تحلل العناصر
        elements_copy = world.elements.copy()
        for element_id in elements_copy:
            if element_id in ELEMENT_RELATIONSHIPS and "decays_to" in ELEMENT_RELATIONSHIPS[element_id]:
                decay_chance = 0.05 * world.regen_rate_scalar
                if random.random() < decay_chance and world.elements.get(element_id, 0) > 0:
                    decay_product = ELEMENT_RELATIONSHIPS[element_id]["decays_to"]
                    decay_amount = max(1, int(world.elements[element_id] * 0.1))
                    if world.elements[element_id] >= decay_amount:
                        world.elements[element_id] -= decay_amount
                        world.elements[decay_product] = world.elements.get(decay_product, 0) + decay_amount
                        
                        if world.elements[element_id] <= 0:
                            del world.elements[element_id]
                        
                        messages.append(f"{decay_amount} من {element_id} تحللت إلى {decay_product}")
        
        return messages

    def enhanced_reproduction(self, world: World) -> List[str]:
        """نظام تكاثر محسن بشروط واقعية"""
        messages = []
        newborns = []
        
        for creature in world.creatures:
            spec = CREATURES.get(creature.spec_id)
            if not spec:
                continue
                
            # شروط التكاثر
            can_reproduce = (
                creature.energy > spec["energy_max"] * 0.7 and
                time.time() - creature.last_breed > 3600 and
                len(world.creatures) + len(newborns) < world.carrying_capacity * 0.8 and
                random.random() < spec["repro"] * world.regen_rate_scalar
            )
            
            if can_reproduce:
                # خسارة الطاقة للتكاثر
                creature.energy *= 0.8
                creature.last_breed = time.time()
                
                # إنتاج نسل
                baby_energy = spec["energy_max"] * random.uniform(0.3, 0.5)
                baby = CreatureInstance(
                    f"{creature.spec_id}_{uuid.uuid4().hex[:6]}",
                    creature.spec_id,
                    baby_energy,
                    0
                )
                newborns.append(baby)
                messages.append(f"{spec['name']} تكاثر وأنتج نسلاً جديداً")
        
        # إضافة المواليد الجدد
        world.creatures.extend(newborns)
        return messages

    def develop_settlements(self, world: World) -> List[str]:
        """تطور المستوطنات في العالم"""
        messages = []
        
        for settlement in world.settlements:
            settlement.update_settlement(time.time())
            
            # بناء عشوائي
            if random.random() < 0.2:
                available_buildings = [b for b in BUILDINGS.keys() if settlement.resources.get("wood", 0) >= 10]
                if available_buildings:
                    building_to_build = random.choice(available_buildings)
                    building_cost = BUILDINGS[building_to_build].get("cost", {})
                    if all(settlement.resources.get(res, 0) >= amount for res, amount in building_cost.items()):
                        for res, amount in building_cost.items():
                            settlement.resources[res] -= amount
                        settlement.buildings[building_to_build] = settlement.buildings.get(building_to_build, 0) + 1
                        messages.append(f"مستوطنة {settlement.name} بنت {building_to_build}")
        
        return messages

    def simulate_settlement_tick(self, settlement: HumanSettlement, world: World = None) -> List[str]:
        """محاكاة تطور المستوطنة"""
        messages = []
        
        # تحديث المستوطنة
        settlement.update_settlement(time.time())
        
        # أحداث عشوائية في المستوطنة
        if random.random() < 0.1:
            event_type = random.choice(["discovery", "problem", "celebration"])
            if event_type == "discovery":
                resource = random.choice(list(BLOCKS.keys()))
                amount = random.randint(1, 5)
                settlement.resources[resource] = settlement.resources.get(resource, 0) + amount
                resource_name = BLOCKS.get(resource, {}).get('name', resource)
                messages.append(f"📜 مستوطنة {settlement.name} اكتشفت {amount} من {resource_name}")
            
            elif event_type == "problem":
                problem = random.choice(["sickness", "attack", "shortage"])
                if problem == "sickness":
                    settlement.population = max(1, settlement.population - 1)
                    messages.append(f"🤒 مرض في مستوطنة {settlement.name}. فقدان سكاني")
                elif problem == "attack" and world:
                    # هجوم مخلوقات على المستوطنة
                    if world.creatures and settlement.defense < 20:
                        damage = random.randint(1, 3)
                        settlement.resources["food"] = max(0, settlement.resources.get("food", 0) - damage)
                        messages.append(f"⚔️ هجوم على مستوطنة {settlement.name}. فقدان {damage} غذاء")
            
            elif event_type == "celebration":
                settlement.happiness = min(100, settlement.happiness + 10)
                messages.append(f"🎉 احتفال في مستوطنة {settlement.name}. زيادة السعادة")
        
        return messages

    def _base_simulation_tick(self, context: Any, params: Dict, ticks: int) -> List[str]:
        messages: List[str] = []
        rnd = random.Random(getattr(context, 'seed', int(time.time())) + int(time.time()))
        
        for tick in range(ticks):
            # نمو العناصر
            for bid, spec in BLOCKS.items():
                if "grow_rate" not in spec:
                    continue
                    
                rate = spec.get("grow_rate", 0.0) * params["regen_scalar"]
                if rate > 0:
                    growth_expect = max(0, (params["size_base"] / 1000.0) * rate * rnd.uniform(0.5, 1.8) * 10)
                    if growth_expect > 0 and random.random() < 0.9:
                        add = max(1, int(growth_expect * rnd.uniform(0.3, 0.9)))
                        context.elements[bid] = context.elements.get(bid, 0) + add
                        if params.get("is_inner_world", False):
                            messages.append(f"في {context.name} نمت {add}× {spec['name']}.")
            
            # تأثيرات البيئة
            if isinstance(context, World) and "effects" in (biome_data := BIOMES.get(context.biome, {})):
                if rnd.random() < biome_data["effects"].get("tick_damage_prob", 0.0):
                    dmg = biome_data["effects"]["tick_damage_amount"]
                    for c in context.creatures:
                        c.energy -= dmg
                    messages.append(f"هالة {biome_data['name']} ألحقت {dmg:.1f} ضرر بكل المخلوقات.")

            # تطبيق العلاقات بين العناصر
            if isinstance(context, World):
                relation_msgs = self.apply_element_relationships(context)
                messages.extend(relation_msgs)
                
                # الافتراس والموارد
                predation_msgs = self.handle_predation_and_resources(context)
                messages.extend(predation_msgs)
                
                # التحلل
                decomposition_msgs = self.decomposition_system(context)
                messages.extend(decomposition_msgs)
                
                # التكاثر المحسن
                reproduction_msgs = self.enhanced_reproduction(context)
                messages.extend(reproduction_msgs)
                
                # تطور المستوطنات
                settlement_msgs = self.develop_settlements(context)
                messages.extend(settlement_msgs)

            # محاكاة المخلوقات الأساسية
            newborns: List[CreatureInstance] = []
            creatures_to_remove = []
            
            for cre in context.creatures:
                spec = CREATURES.get(cre.spec_id)
                if not spec: 
                    continue

                # التغذية
                fed = False
                diet = spec["diet"]
                resource_pool = DIET_TO_RESOURCES.get(diet, [])
                found_food = next((pid for pid in resource_pool if context.elements.get(pid, 0) > 0), None)
                if found_food:
                    context.elements[found_food] -= 1
                    if context.elements[found_food] <= 0: 
                        del context.elements[found_food]
                    cre.energy = min(spec["energy_max"], cre.energy + BLOCKS.get(found_food, {}).get("energy", 0) * 0.9)
                    fed = True
                
                # استهلاك الطاقة والشيخوخة
                cre.energy -= params["energy_decay"]
                cre.age += 1

                # إنتاج Qi في العالم الداخلي
                if params.get("qi_production_enabled", False) and "qi_production" in spec:
                    if cre.energy > spec["energy_max"] * 0.2:
                        produced_qi = spec["qi_production"] * (1 + self.player.level * 0.01)
                        self.player.qi = min(5000.0, self.player.qi + produced_qi)
                        if hasattr(context, 'qi_generation_rate'):
                            context.qi_generation_rate += produced_qi

                # الموت بسبب الطاقة المنخفضة أو العمر
                if cre.energy <= 0 or cre.age > spec.get("lifespan", 1000):
                    creatures_to_remove.append(cre)
                    context.elements["mystic_moss"] = context.elements.get("mystic_moss", 0) + 1
            
            # إزالة المخلوقات الميتة
            for cre in creatures_to_remove:
                if cre in context.creatures:
                    context.creatures.remove(cre)
            
            # إضافة المواليد الجدد
            context.creatures.extend(newborns)
            
            # تتبع الاستقرار البيئي في العالم الداخلي
            if hasattr(context, 'stable_ecosystem_ticks') and len(context.creatures) >= 5:
                context.stable_ecosystem_ticks += 1
                if context.stable_ecosystem_ticks >= 10:
                    if self.player.achievements.check_achievement("ecosystem_balance"):
                        self.player.gain_xp(300)
                        messages.append("🎉 تحقيق إنجاز: توازن بيئي!")
            else:
                context.stable_ecosystem_ticks = 0
                
            context.last_tick = time.time()
        
        return messages

    def simulate_world_tick(self, w: World, ticks: int = 1) -> List[str]:
        params = {
            "regen_scalar": w.regen_rate_scalar,
            "size_base": w.size_cubes,
            "energy_decay": 0.3 * w.difficulty,
            "predation_chance": 0.15,
            "carrying_capacity": w.carrying_capacity,
            "qi_production_enabled": False,
            "tick_interval": CONFIG.get("TICK_INTERVAL_SEC", 6)
        }
        msgs = self._base_simulation_tick(w, params, ticks)
        
        # محاكاة المستوطنات في العالم
        for settlement in w.settlements:
            settlement_msgs = self.simulate_settlement_tick(settlement, w)
            msgs.extend(settlement_msgs)
        
        self.storage.save_world(w)
        return msgs

    def simulate_inner_tick(self, inner: InnerWorld, ticks: int = 1) -> List[str]:
        cultivation_bonus = 1 + (0.1 * (self.player.skills.get("cultivation", 1) - 1))
        params = {
            "regen_scalar": cultivation_bonus,
            "size_base": inner.capacity_cubes,
            "energy_decay": 0.25,
            "predation_chance": 0.12,
            "carrying_capacity": 9999,
            "qi_production_enabled": True,
            "is_inner_world": True,
            "tick_interval": CONFIG.get("TICK_INTERVAL_SEC", 6)
        }
        # Reset Qi generation rate for this tick
        inner.qi_generation_rate = 0.0
        
        msgs = self._base_simulation_tick(inner, params, ticks)
        
        # تطبيق تأثيرات المباني
        building_effects = inner.buildings.get_building_effects()
        for effect, value in building_effects.items():
            inner.effect_system.apply_effect(effect, 3600, value, "buildings")
        
        # تطور المستوطنات الداخلية
        for settlement in inner.settlements:
            settlement_msgs = self.simulate_settlement_tick(settlement)
            msgs.extend(settlement_msgs)
            
            # إنتاج الموارد من المهن
            production = settlement.get_production()
            for resource, amount in production.items():
                # إضافة الإنتاج إلى مخزون اللاعب
                self.player.inventory[resource] = self.player.inventory.get(resource, 0) + amount
            
            # منح خبرة المهارات بناء على الإنتاج
            for profession, count in settlement.professions.items():
                profession_data = PROFESSIONS.get(profession, {})
                required_skills = profession_data.get("required_skills", {})
                
                for skill_name in required_skills.keys():
                    xp_gained = self.skill_system.gain_skill_xp(skill_name, "work", count)
                    self.player.gain_skill_xp(skill_name, xp_gained)
        
        # تحديث السوق
        self.player.economy.update_market()
        
        # تحديث التأثيرات
        self.player.effect_system.update_effects()
        inner.effect_system.update_effects()
        
        # الاستخدام التلقائي للعناصر
        auto_use_msg = self.player.auto_use_items()
        if auto_use_msg:
            msgs.append(auto_use_msg)
            
        self.storage.save_player(self.player.to_dict())
        return msgs

    def tick(self, key: str, ticks: int = 1) -> str:
        if normalize_ar_text(key) in ("داخلي","داخل","inner"):
            msgs = self.simulate_inner_tick(self.player.inner, ticks)
            return "\n".join(msgs) if msgs else f"انتهت محاكاة {ticks} ticks للعالم الداخلي."
        w = self.resolve_world(key)
        if not w: return "العالم غير موجود."
        msgs = self.simulate_world_tick(w, min(ticks, CONFIG.get("MAX_WORLD_TICKS_PER_RUN",3)))
        return "\n".join(msgs) if msgs else f"انتهت محاكاة {ticks} ticks على {w.name}."

    def show_inventory(self) -> str:
        if not self.player.inventory:
            return "المخزن فارغ."
        output = ["📦 مخزنك:"]
        for item_id, quantity in self.player.inventory.items():
            item_name = BLOCKS.get(item_id, {}).get('name', item_id)
            output.append(f"  {item_name}: {quantity}")
        return "\n".join(output)

    def show_stats(self) -> str:
        output = [
             f"👤 {self.player.name} - المستوى {self.player.level}",
             f"📊 XP: {self.player.xp:.1f} / {100 * (1.5 **(self.player.level - 1)):.1f}",
             f"⚡ Qi: {self.player.qi:.1f}",
             f"❤️ HP: {self.player.hp:.1f}",
             f"🌍 العوالم المبتلعة: {self.player.ingested_worlds_count}",
             f"🔧 العناصر المصنوعة: {self.player.crafted_items_count}",
             f"📦 سعة المخزن: {sum(self.player.inventory.values())} عنصر",
             f"🏘️ المستوطنات: {len(self.player.inner.settlements)}"
        ]
        return "\n".join(output)

    def show_achievements(self) -> str:
        output = ["🏆 إنجازاتك:"]
        for achievement_id, achievement_data in self.player.achievements.achievements.items():
            status = "✓" if achievement_data["unlocked"] else "✗"
            progress = ""
            if "count" in achievement_data:
                progress = f" ({achievement_data['count']})"
            output.append(f"{status} {achievement_data['name']}: {achievement_data['desc']}{progress}")
        return "\n".join(output)

    def show_recipes(self) -> str:
        output = ["📜 وصفات الصناعة:"]
        for recipe_id, recipe_data in RECIPES.items():
            ingredients = []
            for item, amount in recipe_data.get("ingredients", {}).items():
                item_name = BLOCKS.get(item, {}).get('name', item)
                ingredients.append(f"{amount}× {item_name}")
        
            outputs = []
            for item, amount in recipe_data.get("output", {}).items():
                item_name = BLOCKS.get(item, {}).get('name', item)
                outputs.append(f"{amount}× {item_name}")
        
            output.append(f"\n{recipe_data.get('name', recipe_id)}:")
            output.append(f"  المكونات: {', '.join(ingredients)}")
            output.append(f"  الناتج: {', '.join(outputs)}")
    
        return "\n".join(output)

    def meditate(self, hours: int) -> str:
        if hours <= 0:
            return "عدد الساعات يجب أن يكون موجباً."
        qi_gain = hours * 10 * (1 + 0.1 * (self.player.skills.get("qi_mastery", 1) - 1))
        self.player.qi = min(5000.0, self.player.qi + qi_gain)

        self.storage.save_player(self.player.to_dict())
        return f"تأملت لمدة {hours} ساعات واستعدت {qi_gain:.1f} Qi."

    def simulate_all_once(self, ticks_per_world: int = 1) -> List[str]:
        msgs = []
        for wid in self.storage.list_worlds():
            w = self.storage.load_world(wid)
            if not w: continue
            try:
                res = self.simulate_world_tick(w, ticks_per_world)
                msgs.extend(res)
            except Exception:
                logging.exception(f"World tick error for {wid}")
        try:
            res_in = self.simulate_inner_tick(self.player.inner, ticks_per_world)
            msgs.extend(res_in)
        except Exception:
            logging.exception("Inner world tick error")
        self._ensure_world_pool()
        if CONFIG.get("SAVE_EVERY_RUN", True):
            self.storage.save_player(self.player.to_dict())
        return msgs

    def close(self):
        self.storage.close()

class SimulationManager(threading.Thread):
    def __init__(self, engine: Engine, interval_sec: int = 6, ticks_each: int = 1):
        super().__init__(daemon=True)
        self.engine = engine
        self.interval = max(1,int(interval_sec))
        self.ticks_each = max(1,int(ticks_each))
        self._stop_event = threading.Event()

    def run(self):
        logging.info("SimulationManager started.")
        last = time.time()
        while not self._stop_event.is_set():
            now = time.time()
            if now - last >= self.interval:
                try:
                    msgs = self.engine.simulate_all_once(self.ticks_each)
                    if msgs:
                        for m in msgs[:12]:
                            logging.debug("Sim: %s", m)
                except Exception:
                    logging.exception("Simulation run failed")
                last = now
            self._stop_event.wait(0.5)

    def stop(self):
        self._stop_event.set()


COMMANDS = {
    "قائمة":"list",
    "عرض":"travel",
    "جمع":"gather","اجمع":"gather",
    "ابتلع":"ingest","ابتلاع":"ingest",
    "ابتلع_مخلوق":"ingest_creature",
    "ابتلع_من_مستوطنة":"ingest_settlement_creatures",
    "مخلوقات":"creatures",
    "زرع":"plant","حصاد":"harvest",
    "هاجم":"attack","هجوم":"attack",
    "تركيب":"mount","فك":"unmount",
    "tick":"tick",
    "داخلي":"inner",
    "مخزن":"inv",
    "احصائيات":"stats",
    "مهارات": "skills",
    "طور": "develop",
    "وصفات": "recipes",
    "اصنع": "craft",
    "بناء": "build_structure",
    "مستوطنة": "create_settlement",
    "مستوطنات": "list_settlements",
    "جمع_موارد": "collect_settlement",
    "بناء_مستوطنة": "build_in_settlement",
    "تعيين_مهنة": "assign_profession",
    "تأمل":"meditate",
    "لقطات":"snapshots",
    "عرض_لقطة":"snapshot","حذف_لقطة":"delete_snapshot",
    "تنظيف_اللقطات":"cleanup_snapshots",
    "تصدير":"export",
    "حفظ":"save","خروج":"exit","مساعدة":"help",
    "إنجازات":"achievements",
    "مهاراتي": "show_skills",
    "تدريب": "train_skill",
    "مهن": "show_professions",
    "تجارة": "trade_item",
    "عملات": "check_currency",
    "إنتاج": "check_production",
    "سمعة": "reputation_info",
    "معلومات_مبنى": "building_info",
    "معلومات_مهنة": "profession_info",
    "السوق": "market_info",
    "تأثيرات": "active_effects"
}

def print_banner():
    print("="*96)
    print(colored_text("Nested Worlds: Reborn", Colors.BOLD + Colors.CYAN))
    print("اكتب 'مساعدة' لعرض الأوامر.")
    print("="*96)

def print_help():
    print("-"*96)
    print(colored_text("الأوامر الأساسية:", Colors.BOLD))
    print("قائمة                                - عرض العوالم المتاحة")
    print("عرض <id|الاسم>                      - وصف عالم")
    print("مخلوقات <id|الاسم>                   - عرض مخلوقات عالم")
    print("جمع <id|الاسم> [عدد]                - جمع موارد (تضاف للمخزن)")
    print("ابتلع <id|الاسم> [عدد]               - ابتلاع مكعبات (يضاف إلى الداخل)")
    print("ابتلع_مخلوق <id|الاسم> <UID>         - ابتلاع مخلوق إلى الداخل")
    print("هاجم <id|الاسم> <UID> [qi_cost]      - هجوم طاقي على مخلوق")
    print("\n" + colored_text("الأوامر الداخلية والزراعة:", Colors.BOLD))
    print("داخلي                                - عرض حالة العالم الداخلي")
    print("زرع <مورد_id> [عدد]                 - زرع موارد من المخزن داخل عالمك")
    print("حصاد <مورد_id> [عدد]                - حصاد موارد من عالمك إلى المخزن")
    print("تركيب <id|الاسم> <نقطة>             - تركيب عالم")
    print("فك <نقطة>                            - إزالة تركيب")
    print("بناء <هيكل_id>                      - بناء هيكل في العالم الداخلي")
    print("مستوطنة <اسم>                        - إنشاء مستوطنة جديدة")
    print("\n" + colored_text("نظام المستوطنات المتكامل:", Colors.BOLD))
    print("مستوطنات                             - عرض جميع المستوطنات")
    print("مستوطنة <اسم>                        - عرض معلومات مستوطنة")
    print("جمع_موارد <مستوطنة> [مورد] [عدد]    - جمع موارد من مستوطنة")
    print("بناء_مستوطنة <مستوطنة> <مبنى>       - بناء مبنى في مستوطنة")
    print("تعيين_مهنة <مستوطنة> <مهنة> <عدد>   - تعيين سكان في مهن")
    print("ابتلع_من_مستوطنة <مستوطنة> [عدد]    - ابتلاع مخلوقات من حول مستوطنة")
    print("إنتاج <مستوطنة>                      - عرض إنتاج المستوطنة")
    print("\n" + colored_text("نظام المهارات والمهن الجديد:", Colors.BOLD))
    print("مهاراتي                              - عرض مهاراتك وتأثيراتها")
    print("تدريب <مهارة> <ساعات>               - تدريب مهارة معينة")
    print("مهن                                  - عرض المهن المتاحة")
    print("معلومات_مهنة <مهنة>                  - عرض متطلبات مهنة")
    print("\n" + colored_text("نظام الاقتصاد والتجارة:", Colors.BOLD))
    print("تجارة <شراء|بيع> <عنصر> [عدد]       - شراء أو بيع عنصر")
    print("عملات                                - عرض عملاتك")
    print("السوق                                - عرض حالة السوق")
    print("\n" + colored_text("نظام التأثيرات والسمعة:", Colors.BOLD))
    print("تأثيرات                              - عرض التأثيرات النشطة")
    print("سمعة                                 - عرض سمعتك مع الفصائل")
    print("معلومات_مبنى <مبنى>                  - عرض معلومات عن مبنى")
    print("\n" + colored_text("التطوير والصناعة:", Colors.BOLD))
    print("احصائيات                             - إحصاءات اللاعب")
    print("مهارات                               - عرض مهاراتك ومستوياتها")
    print("طور <ابتلاع|طاقة|زراعة|قتال|بناء|تجارة> - تطوير مهارة")
    print("وصفات                                - عرض وصفات الصناعة المتاحة")
    print("اصنع <وصفة_id>                      - صناعة عنصر من المكونات")
    print("إنجازات                              - عرض الإنجازات المحققة")
    print("\n" + colored_text("أوامر النظام والمحاكاة:", Colors.BOLD))
    print("مخزن                                 - عرض المستودع")
    print("tick <id|الاسم|داخلي> [n]            - محاكاة يدوية للعالم أو الداخل")
    print("لقطات                                - عرض لقطات محفوظة")
    print("عرض_لقطة <key>                       - عرض محتوى لقطة")
    print("حذف_لقطة <key>                       - حذف لقطة")
    print("تنظيف_اللقطات [N]                   - احتفظ بآخر N لقطة لكل عالم")
    print("تصدير <file.json>                    - تصدير الحالة إلى JSON")
    print("حفظ                                  - حفظ يدوي")
    print("خروج                                 - حفظ وإغلاق")
    print("-"*96)

def repl_loop():
    eng = Engine()
    sim_mgr = None
    if CONFIG.get("BACKGROUND_TICK", True):
        try:
            sim_mgr = SimulationManager(eng, CONFIG.get("TICK_INTERVAL_SEC",6), CONFIG.get("TICKS_PER_RUN",1))
            sim_mgr.start()
        except Exception:
            logging.exception("Failed to start SimulationManager")
    print_banner()
    while True:
        try:
            raw = input("\n> ")
            if not raw: continue
        except (KeyboardInterrupt, EOFError):
            raw = "exit"
        
        parts = parse_input_line(raw.strip())
        if not parts: continue
        cmd_raw = parts[0]
        cmd_key = normalize_ar_text(cmd_raw)
        
        mapped = None
        for k,v in COMMANDS.items():
            if normalize_ar_text(k) == cmd_key:
                mapped = v
                break
        
        if mapped is None and cmd_raw in COMMANDS.values():
            mapped = cmd_raw

        if mapped is None:
            print("أمر غير معروف. اكتب 'مساعدة'.")
            continue

        args = parts[1:]
        try:
            # الأوامر الجديدة
            if mapped == "list_settlements": 
                print(eng.list_settlements())
            elif mapped == "show_settlement": 
                if args:
                    print(eng.show_settlement(" ".join(args)))
                else:
                    print("استخدم: مستوطنة <اسم_المستوطنة>")
            elif mapped == "collect_settlement": 
                if len(args) >= 1:
                    settlement_name = args[0]
                    resource = args[1] if len(args) > 1 else None
                    amount = int(args[2]) if len(args) > 2 else None
                    print(eng.collect_settlement_resources(settlement_name, resource, amount))
                else:
                    print("استخدم: جمع_موارد <اسم_المستوطنة> [المورد] [الكمية]")
            elif mapped == "build_in_settlement": 
                if len(args) >= 2:
                    settlement_name = args[0]
                    building_id = args[1]
                    print(eng.build_in_settlement(settlement_name, building_id))
                else:
                    print("استخدم: بناء_مستوطنة <اسم_المستوطنة> <معرف_المبنى>")
            elif mapped == "ingest_settlement_creatures": 
                if args:
                    settlement_name = args[0]
                    max_creatures = int(args[1]) if len(args) > 1 else None
                    print(eng.ingest_settlement_creatures(settlement_name, max_creatures))
                else:
                    print("استخدم: ابتلع_من_مستوطنة <اسم_المستوطنة> [العدد_الأقصى]")
            elif mapped == "show_skills": 
                print(eng.show_skills())
            elif mapped == "train_skill": 
                if len(args) >= 2:
                    skill = args[0]
                    try:
                        hours = int(args[1])
                        print(eng.train_skill(skill, hours))
                    except ValueError:
                        print("عدد الساعات يجب أن يكون رقماً")
                else:
                    print("استخدم: تدريب <المهارة> <عدد_الساعات>")
            elif mapped == "show_professions": 
                print(eng.show_professions())
            elif mapped == "profession_info":
                if args:
                    profession_system = ProfessionSystem()
                    requirements = profession_system.get_profession_requirements(args[0])
                    if requirements:
                        print(f"متطلبات مهنة {args[0]}:")
                        for skill, level in requirements.items():
                            skill_name = eng.player.get_skill_name(skill)
                            print(f"- {skill_name}: المستوى {level}")
                    else:
                        print("المهنة غير موجودة")
                else:
                    print("استخدم: معلومات_مهنة <معرف_المهنة>")
            elif mapped == "assign_profession":
                if len(args) >= 3:
                    settlement = args[0]
                    profession = args[1]
                    try:
                        count = int(args[2])
                        print(eng.assign_profession(settlement, profession, count))
                    except ValueError:
                        print("العدد يجب أن يكون رقماً")
                else:
                    print("استخدم: تعيين_مهنة <المستوطنة> <المهنة> <العدد>")
            elif mapped == "trade_item":
                if len(args) >= 2:
                    action = args[0]
                    item = args[1]
                    quantity = int(args[2]) if len(args) > 2 else 1
                    print(eng.trade_item(action, item, quantity))
                else:
                    print("استخدم: تجارة <شراء|بيع> <العدد>")
            elif mapped == "check_currency": 
                print(eng.check_currency())
            elif mapped == "check_production": 
                if args:
                    print(eng.check_production(args[0]))
                else:
                    print("استخدم: إنتاج <اسم_المستوطنة>")
            elif mapped == "reputation_info": 
                print(eng.player.show_reputation())
            elif mapped == "building_info":
                if args:
                    print(eng.player.inner.buildings.get_building_info(args[0]))
                else:
                    print("استخدم: معلومات_مبنى <معرف_المبنى>")
            elif mapped == "market_info":
                print(eng.player.economy.get_market_info())
            elif mapped == "active_effects":
                print(eng.player.effect_system.get_active_effects())
            
            # الأوامر القديمة
            elif mapped == "help": 
                print_help()
            elif mapped == "list": 
                [print(l) for l in eng.list_worlds_brief()]
            elif mapped == "travel": 
                print(eng.describe_world(" ".join(args)))
            elif mapped == "creatures": 
                print(eng.list_creatures(" ".join(args)))
            elif mapped == "gather":
                cnt = int(args[1]) if len(args) > 1 else 1
                print(eng.gather(args[0], cnt))
            elif mapped == "ingest":
                cnt = int(args[1]) if len(args) > 1 else None
                print(eng.ingest(args[0], cnt))
            elif mapped == "ingest_creature":
                if len(args) == 1:
                    print(eng.ingest_creature(args[0]))
                else:
                    print(eng.ingest_creature(args[0], args[1]))
            elif mapped == "plant":
                cnt = int(args[1]) if len(args) > 1 else 1
                print(eng.plant(args[0], cnt))
            elif mapped == "harvest":
                cnt = int(args[1]) if len(args) > 1 else 1
                print(eng.harvest(args[0], cnt))
            elif mapped == "attack":
                if len(args) == 1:
                    print(eng.energy_attack(args[0]))
                elif len(args) == 2:
                    print(eng.energy_attack(args[0], args[1]))
                else:
                    ec = float(args[2])
                    print(eng.energy_attack(args[0], args[1], ec))
            elif mapped == "mount": 
                print(eng.mount(args[0], args[1]))
            elif mapped == "unmount": 
                print(eng.unmount(args[0]))
            elif mapped == "build_structure": 
                print(eng.build_structure(args[0]))
            elif mapped == "create_settlement": 
                print(eng.create_settlement(" ".join(args)))
            elif mapped == "tick":
                cnt = int(args[1]) if len(args) > 1 else 1
                print(eng.tick(args[0], cnt))
            elif mapped == "inner":
                inner = eng.player.inner
                print(f"العالم الداخلي: {inner.name} | حجم {inner.size_cubes}/{inner.capacity_cubes} | لقطات {len(inner.ingested_keys)}")
                if inner.elements: 
                    print("عناصر:", ", ".join([f"{BLOCKS.get(k,{'name':k})['name']}({v})" for k,v in inner.elements.items()]))
                if inner.creatures:
                    print("مخلوقات:")
                    for c in inner.creatures:
                        spec = CREATURES.get(c.spec_id)
                        health_percent = c.energy / spec.get("energy_max", 1) * 100
                        health_color = Colors.GREEN if health_percent > 70 else Colors.YELLOW if health_percent > 30 else Colors.RED
                        summon_info = " (مستدعى)" if c.summoned else ""
                        print(f"- [{c.uid}] {spec['name']} ({c.spec_id}) | {colored_text(f'طاقة={c.energy:.1f}', health_color)} | عمر={c.age}{summon_info}")
                if inner.qi_generation_rate > 0:
                    print(f"معدل توليد Qi: {inner.qi_generation_rate:.2f} لكل تيك")
                if inner.stable_ecosystem_ticks > 0:
                    print(f"استقرار بيئي: {inner.stable_ecosystem_ticks}/10 تيكس")
                if inner.buildings.buildings:
                    print("مباني:", ", ".join([f"{k}({v})" for k,v in inner.buildings.buildings.items()]))
                if inner.settlements:
                    print("مستوطنات:")
                    for s in inner.settlements:
                        print(f"- {s.name}: {s.population} سكان، {s.happiness} سعادة")
            elif mapped == "inv":
                print(eng.show_inventory())
            elif mapped == "stats":
                print(eng.show_stats())
            elif mapped == "skills":
                output = ["🎯 مهاراتك:"]
                for sk, lv in eng.player.skills.items():
                    sk_name = eng.player.get_skill_name(sk)
                    xp = eng.player.skill_experience.get(sk, 0.0)
                    needed = lv * 100
                    output.append(f"{sk_name}: المستوى {lv} ({xp:.1f}/{needed} XP)")
                print("\n".join(output))
            elif mapped == "develop":
                if args:
                    print(eng.develop_skill(args[0]))
                else:
                    print("استخدم: طور <اسم المهارة>")
            elif mapped == "recipes":
                print(eng.show_recipes())
            elif mapped == "craft":
                if args:
                    print(eng.craft_item(args[0]))
                else:
                    print("استخدم: اصنع <معرف الوصفة>")
            elif mapped == "meditate":
                hours = int(args[0]) if args else 1
                print(eng.meditate(hours))
            elif mapped == "snapshots":
                print(eng.list_snapshots())
            elif mapped == "snapshot":
                if args:
                    print(eng.show_snapshot(args[0]))
                else:
                    print("استخدم: عرض_لقطة <مفتاح اللقطة>")
            elif mapped == "delete_snapshot":
                if args:
                    print(eng.delete_snapshot(args[0]))
                else:
                    print("استخدم: حذف_لقطة <مفتاح اللقطة>")
            elif mapped == "cleanup_snapshots":
                keep_n = int(args[0]) if args else CONFIG["MAX_SNAPSHOTS_PER_WORLD"]
                print(eng.cleanup_snapshots(keep_n))
            elif mapped == "export":
                fname = args[0] if args else "backup.json"
                print(eng.export_state(fname))
            elif mapped == "save":
                print(eng.save_player())
            elif mapped == "exit":
                if CONFIG.get("AUTOSAVE_ON_EXIT", True):
                    eng.save_player()
                    eng.close()
                if sim_mgr:
                    sim_mgr.stop()
                    print("تم الحفظ والخروج.")
                break
            elif mapped == "achievements":
                print(eng.show_achievements())
            else:
                print("أمر غير معروف. اكتب 'مساعدة'.")

        except Exception as e:
            logging.exception("Command error")
            print(f"خطأ في تنفيذ الأمر: {e}")

    if sim_mgr:
        sim_mgr.stop()
    eng.close()

def main():
    """الدالة الرئيسية لتشغيل اللعبة"""
    try:
        repl_loop()
    except Exception as e:
        logging.exception("حدث خطأ غير متوقع")
        print(f"حدث خطأ غير متوقع: {e}")
        print("يرجى التحقق من ملف السجلات للمزيد من التفاصيل.")

if __name__ == "__main__":
    main()