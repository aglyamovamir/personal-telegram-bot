"""Read-only multilingual search over the existing shared catalog."""
import re
import sqlite3
import unicodedata
import ipaddress
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlsplit
from database import db

# Search vocabulary only. Every selectable result must exist in SQLite.
VOCABULARY = (
    ('bench press', 'жим лёжа', ('жим лежа', 'жим от груди')),
    ('lateral raise', 'разведения в стороны', ('махи в стороны', 'подъемы в стороны')),
    ('curl', 'сгибание рук', ('бицепс', 'подъем на бицепс')),
    ('dips', 'отжимания на брусьях', ('брусья',)),
    ('pull ups', 'подтягивания', ('подтягивание',)),
    ('pull up', 'подтягивания', ('подтягивание',)),
    ('push ups', 'отжимания', ('отжимание',)),
    ('push up', 'отжимания', ('отжимание',)),
    ('deadlift', 'становая тяга', ('становая',)),
    ('squat', 'приседания', ('присед', 'приседание')),
    ('row', 'тяга', ('тяга в наклоне',)),
    ('overhead press', 'жим над головой', ('армейский жим', 'жим стоя')),
    ('shoulder press', 'жим на плечи', ('жим сидя',)),
    ('leg press', 'жим ногами', ()),
    ('leg extension', 'разгибание ног', ()),
    ('leg curl', 'сгибание ног', ()),
    ('calf raise', 'подъём на носки', ('икры',)),
    ('pulldown', 'тяга верхнего блока', ('вертикальная тяга',)),
    ('plank', 'планка', ()),
    ('crunch', 'скручивания', ('пресс',)),
    ('lunge', 'выпады', ()),
    ('hip thrust', 'ягодичный мост', ()),
    ('glute bridge', 'ягодичный мост', ()),
    ('back extension', 'гиперэкстензия', ()),
    ('fly', 'разведения', ('разводка',)),
    ('triceps extension', 'разгибание на трицепс', ('трицепс',)),
)
WORDS = {
    'barbell': 'со штангой', 'dumbbell': 'с гантелями', 'db': 'с гантелями',
    'cable': 'в блоке', 'machine': 'в тренажёре', 'assisted': 'с поддержкой',
    'bench': 'от скамьи', 'incline': 'наклонный', 'decline': 'с наклоном вниз',
    'seated': 'сидя', 'standing': 'стоя', 'single': 'одной', 'arm': 'рукой',
    'leg': 'ногой', 'close': 'узкий', 'grip': 'хват', 'wide': 'широкий',
    'reverse': 'обратный', 'hammer': 'молотковое', 'romanian': 'румынская',
    'bodyweight': 'с собственным весом', 'band': 'с резинкой', 'banded': 'с резинкой',
    'lying': 'лёжа', 'rear': 'задний', 'front': 'передний', 'raise': 'подъём',
}
COMMON = {
    'bench-press': ('Жим штанги лёжа', ('жим лежа', 'жим штанги лежа')),
    'barbell-curl': ('Подъём штанги на бицепс', ('бицепс со штангой', 'сгибание рук со штангой')),
    'bench-dips': ('Обратные отжимания от скамьи', ('отжимания от скамьи',)),
    'dips': ('Отжимания на брусьях', ('брусья',)),
    'lateral-raise': ('Разведения гантелей в стороны', ('разведения в стороны', 'махи в стороны')),
}
LABELS = {
    'barbell': 'Штанга', 'dumbbell': 'Гантели', 'cable': 'Блок',
    'dip_machine': 'Тренажёр для брусьев', 'bodyweight': 'Собственный вес',
    'chest': 'Грудь', 'shoulders': 'Плечи', 'upper_arms': 'Руки',
    'biceps': 'Бицепс', 'triceps': 'Трицепс', 'pectoralis_major': 'Большая грудная',
    'deltoids': 'Дельтовидные', 'quadriceps': 'Квадрицепс', 'hamstrings': 'Задняя поверхность бедра',
    'glutes': 'Ягодичные', 'lats': 'Широчайшие', 'back': 'Спина', 'abs': 'Пресс',
    'rectus_abdominis': 'Прямая мышца живота', 'transverse_abdominis': 'Поперечная мышца живота',
    'anterior_deltoid': 'Передняя дельта', 'lateral_deltoid': 'Средняя дельта',
    'posterior_deltoid': 'Задняя дельта', 'latissimus_dorsi': 'Широчайшие',
    'obliques': 'Косые мышцы живота', 'serratus_anterior': 'Передняя зубчатая',
    'gluteus_maximus': 'Большая ягодичная', 'gluteus_medius': 'Средняя ягодичная',
    'biceps_brachii': 'Бицепс', 'triceps_brachii': 'Трицепс',
    'forearm_flexors': 'Сгибатели предплечья', 'forearm_extensors': 'Разгибатели предплечья',
    'forearms': 'Предплечья', 'rhomboids': 'Ромбовидные', 'trapezius': 'Трапеции',
    'erector_spinae': 'Разгибатели спины', 'gastrocnemius': 'Икроножная',
    'hip_flexors': 'Сгибатели бедра', 'adductors': 'Приводящие бедра',
    'abductors': 'Отводящие бедра', 'soleus': 'Камбаловидная',
    'brachialis': 'Плечевая', 'brachioradialis': 'Плечелучевая',
    'quadratus_lumborum': 'Квадратная мышца поясницы', 'supraspinatus': 'Надостная',
    'ab_wheel': 'Ролик для пресса', 'air_bike': 'Аэробайк', 'pull_up_bar': 'Турник',
    'assisted_pullup_machine': 'Гравитрон', 'stability_ball': 'Фитбол',
    'resistance_band': 'Эспандер', 'loop_band': 'Резиновая петля',
    'battle_rope': 'Канаты', 'lat_pulldown_machine': 'Верхний блок',
    'flat_bench': 'Скамья', 'ez_bar': 'EZ-гриф', 'plyo_box': 'Тумба',
    'chest_press_machine': 'Тренажёр для жима от груди', 'kettlebell': 'Гиря',
    'smith_machine': 'Тренажёр Смита', 'leg_press': 'Тренажёр для жима ногами',
    'dip_station': 'Брусья', 'elliptical': 'Эллипсоид', 'hack_squat': 'Гакк-тренажёр',
    'trap_bar': 'Трэп-гриф', 'hip_abduction_machine': 'Тренажёр для разведения ног',
    'hip_adduction_machine': 'Тренажёр для сведения ног', 'treadmill': 'Беговая дорожка',
    'jump_rope': 'Скакалка', 'leg_curl': 'Тренажёр для сгибания ног',
    'leg_extension': 'Тренажёр для разгибания ног', 'back_extension_machine': 'Гиперэкстензия',
    'bicep_curl_machine': 'Тренажёр для бицепса', 'standing_calf_raise_machine': 'Тренажёр для икр стоя',
    'chest_fly_machine': 'Тренажёр для сведения рук', 'preacher_curl_machine': 'Скамья Скотта',
    'ab_crunch_machine': 'Тренажёр для пресса', 'shoulder_press_machine': 'Тренажёр для жима на плечи',
    'tricep_extension_machine': 'Тренажёр для трицепса', 'slam_ball': 'Медбол',
    'glute_ham_developer': 'GHD-тренажёр', 'pec_deck': 'Бабочка',
    'donkey_calf_raise_machine': 'Тренажёр для икр в наклоне', 'hip_thrust_machine': 'Тренажёр для ягодичного моста',
    'plate_loaded_lateral_raise_machine': 'Тренажёр для разведения рук', 'shrug_machine': 'Тренажёр для шрагов',
    'plates': 'Блины', 'rings': 'Кольца', 'climbing_rope': 'Канат', 'rower': 'Гребной тренажёр',
    'seated_calf_raise_machine': 'Тренажёр для икр сидя', 'sled': 'Сани',
    'stair_climber': 'Степпер', 'stationary_bike': 'Велотренажёр',
    'suspension_trainer': 'Петли TRX', 'wrist_roller': 'Кистевой тренажёр',
}


def normalize(text):
    return ' '.join(re.findall(r'[^\W_]+', unicodedata.normalize('NFKC', text or '').casefold().replace('ё', 'е')))


def names(external, ru, en):
    normalized = normalize(en)
    aliases = []
    translated = normalized
    for phrase, russian, alternatives in VOCABULARY:
        if phrase == 'curl' and ('leg' in normalized or 'hamstring' in normalized):
            continue
        if phrase == 'dips' and ('bench' in normalized or 'crab' in normalized):
            continue
        pattern = r'(?<!\w)' + re.escape(phrase) + r's?(?!\w)'
        if re.search(pattern, normalized):
            aliases.extend((russian, *alternatives))
            translated = re.sub(pattern, russian, translated)
    translated = ' '.join(WORDS.get(word, word) for word in translated.split())
    preferred, extra = COMMON.get(external, (None, ()))
    display = (ru or '').strip() or preferred or (translated.capitalize() if translated != normalized else en)
    return display, [display, en, translated, *aliases, *extra]


def score(query, candidates, exact_aliases=()):
    best = None
    for index, candidate in enumerate(candidates):
        text = normalize(candidate)
        if query == text:
            explicit = query in {normalize(alias) for alias in exact_aliases}
            rank = (0, 0) if index < 2 else (1, 0 if explicit else len(normalize(candidates[0])))
        elif text.startswith(query):
            rank = (2, len(text) - len(query))
        elif all(any(word.startswith(token) for word in text.split()) for token in query.split()):
            rank = (3, len(text))
        elif query in text:
            rank = (4, len(text))
        else:
            tokens = query.split()
            similarities = [max((SequenceMatcher(None, token, w).ratio() for w in text.split()), default=0) for token in tokens]
            similarity = sum(similarities) / len(tokens)
            if len(query) < 4 or min(similarities) < .65 or similarity < .78:
                continue
            rank = (5, -similarity)
        if best is None or rank < best:
            best = rank
    return best


def search_exercises(query, page=0, page_size=8):
    query = normalize(query)
    if not 2 <= len(query) <= 100:
        raise ValueError('Введите от 2 до 100 символов названия')
    if type(page) is not int or page < 0 or not 1 <= page_size <= 20:
        raise ValueError('Неверная страница поиска')
    with db._connection() as c:
        rows = c.execute('SELECT external_id,name_ru,name_en FROM exercises ORDER BY id').fetchall()
    matches = []
    for external, ru, en in rows:
        display, candidates = names(external, ru, en)
        rank = score(query, candidates, COMMON.get(external, (None, ()))[1])
        if rank is not None:
            matches.append((rank, normalize(display), dict(exercise_id=external, name=display, name_en=en)))
    matches.sort(key=lambda row: (row[0], row[1], row[2]['exercise_id']))
    page = min(page, max(0, (len(matches) - 1) // page_size))
    return dict(page=page, total=len(matches), items=[r[2] for r in matches[page * page_size:(page + 1) * page_size]])


def image_source(value):
    """Validate references; local files must stay inside the existing image root."""
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme in ('http', 'https'):
        if (not parsed.hostname or parsed.username or parsed.password
                or parsed.hostname.lower() == 'localhost' or any(c.isspace() for c in value)
                or port == 0):
            return None
        try:
            if not ipaddress.ip_address(parsed.hostname).is_global:
                return None
        except ValueError:
            if '.' not in parsed.hostname:
                return None
        return value
    if parsed.scheme or parsed.netloc:
        return None
    root = (Path(__file__).resolve().parents[1] / 'data' / 'repdb').resolve()
    try:
        path = (root / value).resolve()
    except (OSError, ValueError):
        return None
    if root not in path.parents or path.suffix.lower() not in {'.jpg', '.jpeg', '.png', '.webp'}:
        return None
    return path if path.is_file() else None


def get_exercise(external):
    with db._connection() as c:
        c.row_factory = sqlite3.Row
        row = c.execute('SELECT * FROM exercises WHERE external_id=?', (external,)).fetchone()
        if row is None:
            raise ValueError('Упражнение больше недоступно. Повторите поиск.')
        data = dict(row)
        muscles = c.execute("SELECT muscle FROM exercise_muscles WHERE exercise_id=? AND muscle_type='primary' ORDER BY muscle", (data['id'],)).fetchall()
        paths = c.execute('SELECT path FROM exercise_images WHERE exercise_id=? ORDER BY id', (data['id'],)).fetchall()
    data['name'], _ = names(external, data.get('name_ru'), data['name_en'])
    data['muscles'] = [LABELS.get(r[0], r[0].replace('_', ' ')) for r in muscles[:5]]
    data['equipment_label'] = LABELS.get(data.get('equipment'), data.get('equipment') or 'Не указано')
    data['image'] = next((source for value in [data.get('image_url'), *(r[0] for r in paths)] if (source := image_source(value)) is not None), None)
    return data
