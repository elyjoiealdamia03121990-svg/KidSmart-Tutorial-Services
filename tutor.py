"""KidSmart AI Tutor - lesson engine.

make_round() returns a round of 5 tap-to-answer items. It asks Claude (Haiku) for an
adaptive round when ANTHROPIC_API_KEY is set, and falls back to the built-in question
banks below when there is no key, the AI is switched off, or the AI call fails.

Item format (what the app receives):
  {"say": spoken prompt, "text": big text on screen (emoji/word/short passage),
   "choices": [2-4 short strings], "answer": index of the right choice,
   "correct": spoken praise, "wrong": spoken hint, "skill": short tag}
"{name}" in any spoken line is replaced on the device with the child's first name,
so the child's name is never sent to the AI.
"""
import os, json, random, re, urllib.request, urllib.error

LEVELS = ['Preschool', 'Kinder', 'Grade 1', 'Grade 2', 'Grade 3', 'Grade 4']

SUBJECTS = [
    {'id': 'phonics', 'name': 'Letters & Phonics', 'emoji': '🔤', 'color': '#FF6B5B', 'lang': 'en', 'min': 1, 'max': 3},
    {'id': 'reading', 'name': 'English Reading', 'emoji': '📖', 'color': '#4C8DF6', 'lang': 'en', 'min': 2, 'max': 6},
    {'id': 'math', 'name': 'Math', 'emoji': '🔢', 'color': '#20A99A', 'lang': 'en', 'min': 1, 'max': 6},
    {'id': 'shapes', 'name': 'Shapes & Colors', 'emoji': '🔺', 'color': '#FFB627', 'lang': 'en', 'min': 1, 'max': 2},
    {'id': 'filipino', 'name': 'Filipino', 'emoji': '🇵🇭', 'color': '#9B5DE5', 'lang': 'fil', 'min': 2, 'max': 6},
    {'id': 'science', 'name': 'Science', 'emoji': '🔬', 'color': '#2E9E5B', 'lang': 'en', 'min': 3, 'max': 6},
]
SUBJ = {s['id']: s for s in SUBJECTS}

PRAISE = {'en': ['Great job, {name}!', 'Yes! You got it!', 'Wow, super!', 'Correct! Nice thinking!', 'Excellent, {name}!'],
          'fil': ['Galing mo, {name}!', 'Tama! Ang husay!', 'Wow, napakagaling!', 'Tama ka, {name}!', 'Ang talino mo!']}
TRY = {'en': ['Good try!', 'Almost!', 'Nice try, {name}!'], 'fil': ['Magaling ang pagsubok!', 'Muntik na!', 'Sige, kaya mo yan!']}

def clamp_level(subject, level):
    s = SUBJ[subject]
    return max(s['min'], min(s['max'], int(level or s['min'])))

def start_level(subject, grade_index):
    """grade_index: 1=Preschool ... 6=Grade 4"""
    return clamp_level(subject, grade_index)

def subjects_for(grade_index):
    """Subjects that fit a child's grade (allow one band below/above)."""
    return [s for s in SUBJECTS if s['min'] - 1 <= grade_index <= s['max'] + 1]

# ---------------------------------------------------------------- helpers
def item(say, text, choices, answer, correct, wrong, skill=''):
    return {'say': say, 'text': text, 'choices': [str(c) for c in choices], 'answer': answer,
            'correct': correct, 'wrong': wrong, 'skill': skill}

def mc(r, right, pool, n=3):
    """right answer + n-1 distractors from pool, answer index 0 (shuffled later)."""
    others = [p for p in dict.fromkeys(pool) if p != right]
    r.shuffle(others)
    return [right] + others[:n - 1]

def near(r, x, n=3, lo=0, spread=3):
    out, tries = [x], 0
    while len(out) < n and tries < 50:
        tries += 1
        y = x + r.choice([d for d in range(-spread, spread + 1) if d])
        if y >= lo and y not in out:
            out.append(y)
    while len(out) < n:
        out.append(out[-1] + 1)
    return out

def shuffle_item(it, r):
    order = list(range(len(it['choices'])))
    r.shuffle(order)
    it['choices'] = [it['choices'][i] for i in order]
    it['answer'] = order.index(it['answer'])
    return it

# ---------------------------------------------------------------- word banks
LETTER_WORDS = {'A': ('apple', '🍎'), 'B': ('bee', '🐝'), 'C': ('cat', '🐱'), 'D': ('dog', '🐶'), 'E': ('egg', '🥚'),
    'F': ('fish', '🐟'), 'G': ('goat', '🐐'), 'H': ('hat', '🎩'), 'I': ('insect', '🐞'), 'J': ('juice', '🧃'),
    'K': ('kite', '🪁'), 'L': ('lion', '🦁'), 'M': ('moon', '🌙'), 'N': ('nose', '👃'), 'O': ('octopus', '🐙'),
    'P': ('pig', '🐷'), 'Q': ('queen', '👸'), 'R': ('rabbit', '🐰'), 'S': ('sun', '☀️'), 'T': ('tree', '🌳'),
    'U': ('umbrella', '☂️'), 'V': ('van', '🚐'), 'W': ('watch', '⌚'), 'Y': ('yo-yo', '🪀'), 'Z': ('zebra', '🦓')}
CVC = [('cat', '🐱'), ('dog', '🐶'), ('pig', '🐷'), ('hat', '🎩'), ('sun', '☀️'), ('bus', '🚌'), ('cup', '☕'),
       ('bed', '🛏️'), ('box', '📦'), ('fox', '🦊'), ('hen', '🐔'), ('bug', '🐛'), ('map', '🗺️'), ('pen', '🖊️'),
       ('van', '🚐'), ('web', '🕸️'), ('log', '🪵'), ('bat', '🦇'), ('rat', '🐀'), ('jet', '✈️')]
SIGHT = ['the', 'a', 'I', 'is', 'see', 'can', 'my', 'we', 'go', 'to', 'and', 'it', 'in', 'me', 'you', 'up', 'look', 'he', 'she', 'like']
RHYMES = [['cat', 'hat', 'bat', 'mat', 'rat'], ['dog', 'log', 'frog', 'fog'], ['sun', 'bun', 'run', 'fun'],
          ['pig', 'wig', 'dig', 'big'], ['bug', 'rug', 'mug', 'hug'], ['hen', 'pen', 'ten', 'men'],
          ['cake', 'lake', 'bake', 'make'], ['ball', 'tall', 'wall', 'fall'], ['star', 'car', 'jar', 'far']]
PLURALS = [('cat', 'cats'), ('dog', 'dogs'), ('box', 'boxes'), ('bus', 'buses'), ('baby', 'babies'), ('book', 'books'),
           ('dish', 'dishes'), ('city', 'cities'), ('child', 'children'), ('mouse', 'mice'), ('foot', 'feet'), ('man', 'men')]
OPPOSITES = [('big', 'small'), ('hot', 'cold'), ('up', 'down'), ('happy', 'sad'), ('fast', 'slow'), ('day', 'night'),
             ('open', 'close'), ('tall', 'short'), ('old', 'new'), ('full', 'empty'), ('wet', 'dry'), ('in', 'out')]
PAST = [('play', 'played'), ('jump', 'jumped'), ('walk', 'walked'), ('cook', 'cooked'), ('run', 'ran'), ('eat', 'ate'),
        ('see', 'saw'), ('go', 'went'), ('swim', 'swam'), ('write', 'wrote'), ('sing', 'sang'), ('drink', 'drank')]
WRONG_PAST = {'run': ['runned', 'runs'], 'eat': ['eated', 'eats'], 'see': ['seed', 'sees'], 'go': ['goed', 'goes'],
              'swim': ['swimmed', 'swims'], 'write': ['writed', 'writes'], 'sing': ['singed', 'sings'], 'drink': ['drinked', 'drinks']}
NOUNS = ['dog', 'table', 'mango', 'teacher', 'school', 'jeepney', 'book', 'river', 'pencil', 'mother']
VERBS = ['run', 'eat', 'swim', 'jump', 'write', 'sing', 'cook', 'read', 'dance', 'sleep']
ADJS = ['tall', 'red', 'happy', 'sweet', 'little', 'noisy', 'soft', 'cold', 'pretty', 'brave']
SYNONYMS = [('big', 'large'), ('happy', 'glad'), ('small', 'tiny'), ('fast', 'quick'), ('begin', 'start'),
            ('smart', 'clever'), ('shout', 'yell'), ('pretty', 'beautiful'), ('sad', 'unhappy'), ('finish', 'end')]
PASSAGES_EN = [
    (4, 'Ben has a red ball. He plays with his dog, Max. Max runs fast to get the ball.',
     [('What color is the ball?', 'red', ['blue', 'green']), ('Who is Max?', 'a dog', ['a cat', 'a boy'])]),
    (4, 'It is raining. Ana takes her yellow umbrella. She walks to school with her brother.',
     [('What is the weather?', 'rainy', ['sunny', 'windy']), ('Where does Ana go?', 'to school', ['to the market', 'to the park'])]),
    (5, 'Lito planted a mango seed. He watered it every morning. After many weeks, a small plant grew.',
     [('What did Lito plant?', 'a mango seed', ['a flower', 'a rice seed']), ('When did he water it?', 'every morning', ['every night', 'once a week'])]),
    (5, 'The jeepney was full. Mia stood near the door and held on tight. She paid the driver twenty pesos.',
     [('Why did Mia stand?', 'The jeepney was full', ['She was tired', 'She was late']), ('How much did she pay?', 'twenty pesos', ['ten pesos', 'fifty pesos'])]),
    (6, 'Carlo forgot his lunch at home. His friend Joy shared her rice and fish. Carlo thanked her and promised to bring snacks tomorrow.',
     [('What problem did Carlo have?', 'He forgot his lunch', ['He lost his bag', 'He was sick']), ('What kind of friend is Joy?', 'kind', ['selfish', 'lazy'])]),
    (6, 'Bees fly from flower to flower to collect nectar. They carry pollen on their legs. This helps new plants and fruits to grow.',
     [('What do bees collect?', 'nectar', ['water', 'leaves']), ('How do bees help plants?', 'They carry pollen', ['They eat leaves', 'They give shade'])]),
]

FIL_WORDS = [('aso', '🐕'), ('bola', '⚽'), ('baka', '🐄'), ('isda', '🐟'), ('ibon', '🐦'), ('mangga', '🥭'), ('saging', '🍌'),
             ('bahay', '🏠'), ('puno', '🌳'), ('araw', '☀️'), ('buwan', '🌙'), ('pusa', '🐱'), ('manok', '🐔'), ('kotse', '🚗'),
             ('gatas', '🥛'), ('tinapay', '🍞'), ('itlog', '🥚'), ('ulan', '🌧️'), ('susi', '🔑'), ('lapis', '✏️'),
             ('aklat', '📖'), ('unggoy', '🐒'), ('kabayo', '🐴'), ('dahon', '🍃'), ('bulaklak', '🌸'), ('payong', '☂️'), ('sapatos', '👟')]
FIL_COLORS = [('pula', '🔴'), ('asul', '🔵'), ('dilaw', '🟡'), ('berde', '🟢'), ('puti', '⚪'), ('itim', '⚫'), ('kahel', '🟠'), ('lila', '🟣')]
FIL_COLOR_OBJ = [('🍌', 'saging', 'dilaw'), ('🍎', 'mansanas', 'pula'), ('🍃', 'dahon', 'berde'), ('🌊', 'dagat', 'asul'), ('🥥', 'niyog', 'kayumanggi'), ('☁️', 'ulap', 'puti')]
FIL_NUM = ['isa', 'dalawa', 'tatlo', 'apat', 'lima', 'anim', 'pito', 'walo', 'siyam', 'sampu']
FIL_OPP = [('malaki', 'maliit'), ('mataas', 'mababa'), ('mainit', 'malamig'), ('mabilis', 'mabagal'), ('masaya', 'malungkot'),
           ('bago', 'luma'), ('mahaba', 'maikli'), ('malinis', 'marumi'), ('mabigat', 'magaan'), ('maaga', 'huli')]
FIL_SYN = [('maganda', 'marikit'), ('masaya', 'maligaya'), ('malaki', 'malawak'), ('matalino', 'marunong'), ('mabilis', 'matulin'),
           ('takot', 'kaba'), ('galit', 'poot'), ('tahanan', 'bahay'), ('mayaman', 'masagana'), ('kaibigan', 'katoto')]
FIL_NOUN = ['aso', 'paaralan', 'guro', 'mesa', 'lapis', 'nanay', 'dagat', 'bundok']
FIL_VERB = ['tumakbo', 'kumain', 'sumayaw', 'naglaro', 'lumangoy', 'sumulat', 'umawit', 'natulog']
FIL_ADJ = ['maganda', 'matangkad', 'masipag', 'malinis', 'matamis', 'mabait', 'maingay', 'malamig']
FIL_ASPECT = [('laro', 'naglaro', 'naglalaro', 'maglalaro'), ('kain', 'kumain', 'kumakain', 'kakain'),
              ('basa', 'nagbasa', 'nagbabasa', 'magbabasa'), ('luto', 'nagluto', 'nagluluto', 'magluluto'), ('sulat', 'sumulat', 'sumusulat', 'susulat')]
FIL_PASSAGES = [
    (5, 'Si Lola ay nagluto ng adobo. Kumain kami nang sabay-sabay. Masaya ang buong pamilya.',
     [('Ano ang niluto ni Lola?', 'adobo', ['sinigang', 'tinola']), ('Ano ang naramdaman ng pamilya?', 'masaya', ['malungkot', 'galit'])]),
    (6, 'Maagang gumising si Ramon. Tinulungan niya ang kanyang tatay sa bukid. Nagtanim sila ng palay hanggang tanghali.',
     [('Sino ang tinulungan ni Ramon?', 'ang tatay niya', ['ang guro niya', 'ang kapatid niya']), ('Ano ang itinanim nila?', 'palay', ['mais', 'saging'])]),
]

SCIENCE = {
    3: [('🐟', 'Where does a fish live?', 'in water', ['in a tree', 'in the sand']),
        ('🐄', 'What do cows give us?', 'milk', ['eggs', 'honey']),
        ('👂', 'Which part of the body do we use to hear?', 'ears', ['eyes', 'nose']),
        ('👀', 'Which part of the body do we use to see?', 'eyes', ['hands', 'ears']),
        ('🌱', 'What do plants need to grow?', 'water and sunlight', ['candy', 'toys']),
        ('🌧️', 'What falls from clouds when it rains?', 'water', ['sand', 'leaves']),
        ('🐔', 'Which animal lays eggs?', 'hen', ['dog', 'cow']),
        ('🦷', 'What should we do after eating?', 'brush our teeth', ['sleep right away', 'run fast'])],
    4: [('🧊', 'Ice is a...', 'solid', ['liquid', 'gas']),
        ('💧', 'Water in a glass is a...', 'liquid', ['solid', 'gas']),
        ('🐸', 'A frog is an...', 'amphibian', ['insect', 'bird']),
        ('🌳', 'Which part of the plant holds it in the soil?', 'roots', ['flowers', 'leaves']),
        ('🦋', 'A caterpillar grows into a...', 'butterfly', ['bee', 'spider']),
        ('🍎', 'Which food helps us grow strong?', 'fruits and vegetables', ['soft drinks', 'chips']),
        ('🧲', 'A magnet can pull...', 'iron nails', ['paper', 'wood']),
        ('☀️', 'What gives Earth light and heat?', 'the sun', ['the moon', 'the clouds'])],
    5: [('🫁', 'Which organ helps us breathe?', 'lungs', ['stomach', 'heart']),
        ('❤️', 'Which organ pumps blood?', 'heart', ['lungs', 'brain']),
        ('🌍', 'Earth turns around once every...', 'day', ['month', 'year']),
        ('🌋', 'A mountain that can erupt is a...', 'volcano', ['hill', 'island']),
        ('🐢', 'A sea turtle is a...', 'reptile', ['fish', 'mammal']),
        ('💨', 'Steam from boiling water is a...', 'gas', ['solid', 'liquid']),
        ('🍃', 'Plants make food in their...', 'leaves', ['roots', 'seeds']),
        ('🦴', 'Bones protect our body and help us...', 'move', ['taste', 'smell'])],
    6: [('🪐', 'Which planet is the biggest?', 'Jupiter', ['Mars', 'Earth']),
        ('🌎', 'Which planet do we live on?', 'Earth', ['Venus', 'Saturn']),
        ('🌀', 'A strong storm with heavy rain and wind in the Philippines is a...', 'typhoon', ['drought', 'rainbow']),
        ('🔋', 'A battery gives...', 'electricity', ['water', 'air']),
        ('🧪', 'Mixing salt in water makes a...', 'solution', ['solid', 'gas']),
        ('🐝', 'Animals that eat only plants are called...', 'herbivores', ['carnivores', 'omnivores']),
        ('🦁', 'Animals that eat only meat are called...', 'carnivores', ['herbivores', 'producers']),
        ('🌡️', 'We use a thermometer to measure...', 'temperature', ['length', 'weight'])],
}

COUNT_EMOJI = ['🍎', '🐟', '⭐', '🐥', '🎈', '🍌', '🚗', '🌸', '🐞', '🥭']
COLORS = [('red', '🔴'), ('blue', '🔵'), ('yellow', '🟡'), ('green', '🟢'), ('orange', '🟠'), ('purple', '🟣'), ('black', '⚫'), ('white', '⚪'), ('brown', '🟤')]
SHAPES = [('circle', '⚪'), ('triangle', '🔺'), ('square', '🟥'), ('star', '⭐'), ('heart', '❤️'), ('diamond', '🔷')]
SIDES = [('triangle', 3, '🔺'), ('square', 4, '🟥'), ('rectangle', 4, '▬'), ('pentagon', 5, '⬠'), ('hexagon', 6, '⬡')]
COLOR_OBJ = [('🍌', 'banana', 'yellow'), ('🍎', 'apple', 'red'), ('🍃', 'leaf', 'green'), ('🥕', 'carrot', 'orange'),
             ('🍇', 'grapes', 'purple'), ('☁️', 'cloud', 'white'), ('🍫', 'chocolate', 'brown')]

# ---------------------------------------------------------------- generators (one item each)
def g_phonics(level, r):
    letters = list(LETTER_WORDS)
    L = r.choice(letters)
    word, pic = LETTER_WORDS[L]
    p, t = r.choice(PRAISE['en']), r.choice(TRY['en'])
    if level == 1:
        return item(f'Find the letter {L}.', 'Find: ' + L, mc(r, L, letters), 0, f'{p} That is {L}!', f'{t} This is the letter {L}.', 'letter')
    if level == 2:
        if r.random() < .5:
            return item(f'Which is small {L}?', L + ' → ?', mc(r, L.lower(), [x.lower() for x in letters]), 0,
                        f'{p} Big {L}, small {L.lower()}.', f'{t} Small {L} looks like this: {L.lower()}.', 'lowercase')
        return item(f'{word}. What letter does {word} start with?', pic + ' ' + word, mc(r, L, letters), 0,
                    f'{p} {word} starts with {L}.', f'{t} {word} starts with {L}.', 'beginning sound')
    if r.random() < .5:
        w, pic = r.choice(CVC)
        v = w[1]
        return item(f'{w}. Which letter is missing?', f'{pic}  {w[0]} _ {w[2]}', mc(r, v, list('aeiou')), 0,
                    f'{p} {w} has the letter {v}.', f'{t} {w}: {w[0]}, {v}, {w[2]}.', 'vowel')
    others = [k for k in letters if k != L]
    r.shuffle(others)
    opts = [pic] + [LETTER_WORDS[k][1] for k in others[:2]]
    return item(f'Which picture starts with the letter {L}?', 'Starts with ' + L, opts, 0,
                f'{p} {word} starts with {L}.', f'{t} {word} starts with {L}.', 'beginning sound')

def g_reading(level, r):
    p, t = r.choice(PRAISE['en']), r.choice(TRY['en'])
    if level <= 2:
        if r.random() < .5:
            w = r.choice(SIGHT)
            return item(f'Find the word: {w}.', 'Find: ' + w, mc(r, w, SIGHT), 0, f'{p} That says {w}.', f'{t} This word is {w}.', 'sight word')
        w, pic = r.choice(CVC)
        return item('Which word matches the picture?', pic, mc(r, w, [c[0] for c in CVC]), 0, f'{p} It is a {w}.', f'{t} This is a {w}.', 'word match')
    if level == 3:
        k = r.random()
        if k < .35:
            grp = r.choice(RHYMES)
            base, right = r.sample(grp, 2)
            pool = [w for g2 in RHYMES if g2 is not grp for w in g2]
            return item(f'What rhymes with {base}?', base, mc(r, right, pool), 0, f'{p} {base} and {right} rhyme!', f'{t} {base} rhymes with {right}.', 'rhyme')
        if k < .7:
            w, pic = r.choice(CVC)
            return item(f'I see a ... Which word fits?', f'I see a ___.  {pic}', mc(r, w, [c[0] for c in CVC]), 0, f'{p} I see a {w}.', f'{t} I see a {w}.', 'sentence')
        a, b = r.choice(OPPOSITES)
        return item(f'What is the opposite of {a}?', a + ' ↔ ?', mc(r, b, [o[1] for o in OPPOSITES] + [o[0] for o in OPPOSITES if o[0] != a]), 0,
                    f'{p} The opposite of {a} is {b}.', f'{t} The opposite of {a} is {b}.', 'opposites')
    k = r.random()
    if k < .4:
        pas = [x for x in PASSAGES_EN if x[0] <= level]
        _, text, qs = r.choice(pas)
        q, right, wrong = r.choice(qs)
        return item('Read the story, then answer. ' + q, text + '\n\n' + q, [right] + wrong, 0, f'{p} {right}.', f'{t} Read again. The answer is: {right}.', 'comprehension')
    if level == 4:
        if r.random() < .5:
            s, pl = r.choice(PLURALS)
            wrongs = list(dict.fromkeys([s + 's', s + 'es', s + 'ies'] if s[-1] != 'y' else [s + 's', s[:-1] + 'ies', s + 'es']))
            wrongs = [w for w in wrongs if w != pl][:2]
            return item(f'One {s}, two ...?', f'one {s} → two ___', [pl] + wrongs, 0, f'{p} Two {pl}.', f'{t} One {s}, two {pl}.', 'plurals')
        a, b = r.choice(OPPOSITES)
        return item(f'What is the opposite of {a}?', a + ' ↔ ?', mc(r, b, [o[1] for o in OPPOSITES]), 0, f'{p} {a} and {b} are opposites.', f'{t} The opposite of {a} is {b}.', 'opposites')
    if level == 5:
        k = r.random()
        if k < .35:
            base, past = r.choice(PAST)
            wr = WRONG_PAST.get(base, [base + 's', base + 'ing'])
            return item(f'Today I {base}. Yesterday I ...?', f'Yesterday I ___. ({base})', [past] + wr, 0, f'{p} Yesterday I {past}.', f'{t} The past of {base} is {past}.', 'past tense')
        if k < .7:
            v = r.choice(VERBS)
            return item('Which word is an action word?', 'Action word?', [v] + r.sample(NOUNS, 2), 0, f'{p} {v} is an action word, a verb.', f'{t} {v} is something we do. It is a verb.', 'verbs')
        a, b = r.choice(SYNONYMS)
        return item(f'Which word means the same as {a}?', a + ' = ?', mc(r, b, [s[1] for s in SYNONYMS]), 0, f'{p} {a} and {b} mean the same.', f'{t} {a} means {b}.', 'synonyms')
    k = r.random()
    if k < .35:
        adj = r.choice(ADJS)
        return item('Which word describes a person or thing?', 'Describing word?', [adj] + r.sample(VERBS, 2), 0, f'{p} {adj} is an adjective.', f'{t} {adj} tells what something is like.', 'adjectives')
    if k < .7:
        subj, right, wrong = r.choice([('She', 'plays', 'play'), ('They', 'play', 'plays'), ('The dog', 'runs', 'run'),
                                       ('We', 'are', 'is'), ('He', 'is', 'are'), ('The birds', 'sing', 'sings')])
        return item(f'{subj} ... Which word is correct?', f'{subj} ___ every day.', [right, wrong], 0, f'{p} {subj} {right}.', f'{t} We say: {subj} {right}.', 'agreement')
    s = r.choice([('Where are you going', '?', ['.', '!']), ('I like mangoes', '.', ['?', ',']), ('Wow, that is amazing', '!', ['?', ','])])
    return item('Which mark ends this sentence?', s[0] + ' ___', [s[1]] + s[2], 0, f'{p} {s[0]}{s[1]}', f'{t} It ends with {s[1]}', 'punctuation')

def g_math(level, r):
    p, t = r.choice(PRAISE['en']), r.choice(TRY['en'])
    e = r.choice(COUNT_EMOJI)
    if level == 1:
        if r.random() < .7:
            n = r.randint(1, 5)
            return item('How many can you count?', e * n, near(r, n, 3, 1, 2), 0, f'{p} There are {n}.', f'{t} Let us count: ' + ', '.join(str(i) for i in range(1, n + 1)) + '.', 'counting')
        a, b = r.sample(range(1, 6), 2)
        return item('Which group has more?', f'{e * a}   or   {e * b}', [str(max(a, b)), str(min(a, b))], 0, f'{p} {max(a, b)} is more.', f'{t} {max(a, b)} is more than {min(a, b)}.', 'more or less')
    if level == 2:
        k = r.random()
        if k < .35:
            n = r.randint(4, 10)
            return item('How many?', e * n, near(r, n, 3, 1, 2), 0, f'{p} There are {n}.', f'{t} Count slowly. There are {n}.', 'counting')
        if k < .65:
            a, b = r.randint(1, 4), r.randint(1, 4)
            return item(f'{a} plus {b} is?', f'{e * a} + {e * b}', near(r, a + b, 3, 1, 2), 0, f'{p} {a} + {b} = {a + b}.', f'{t} {a} + {b} = {a + b}.', 'addition')
        n = r.randint(1, 8)
        return item('What number comes next?', f'{n}, {n + 1}, ___', near(r, n + 2, 3, 1, 2), 0, f'{p} {n + 2} comes next.', f'{t} After {n + 1} comes {n + 2}.', 'number order')
    if level == 3:
        k = r.random()
        if k < .4:
            a, b = r.randint(2, 12), r.randint(1, 8)
            return item(f'{a} plus {b}?', f'{a} + {b} = ?', near(r, a + b, 3, 0, 3), 0, f'{p} {a} + {b} = {a + b}.', f'{t} {a} + {b} = {a + b}.', 'addition')
        if k < .75:
            a = r.randint(5, 20); b = r.randint(1, a)
            return item(f'{a} minus {b}?', f'{a} − {b} = ?', near(r, a - b, 3, 0, 3), 0, f'{p} {a} − {b} = {a - b}.', f'{t} {a} − {b} = {a - b}.', 'subtraction')
        step = r.choice([2, 5, 10]); s = step * r.randint(1, 4)
        return item(f'Count by {step}s. What comes next?', f'{s}, {s + step}, {s + 2 * step}, ___', near(r, s + 3 * step, 3, 0, step), 0,
                    f'{p} {s + 3 * step}!', f'{t} Add {step} each time: {s + 3 * step}.', 'skip counting')
    if level == 4:
        k = r.random()
        if k < .3:
            a, b = r.randint(10, 60), r.randint(10, 39)
            return item(f'{a} plus {b}?', f'{a} + {b} = ?', near(r, a + b, 3, 0, 10 if r.random() < .5 else 2), 0, f'{p} {a + b}.', f'{t} {a} + {b} = {a + b}.', '2-digit addition')
        if k < .55:
            a = r.randint(30, 99); b = r.randint(10, a - 5)
            return item(f'{a} minus {b}?', f'{a} − {b} = ?', near(r, a - b, 3, 0, 10 if r.random() < .5 else 2), 0, f'{p} {a - b}.', f'{t} {a} − {b} = {a - b}.', '2-digit subtraction')
        if k < .75:
            n = r.randint(11, 99)
            return item(f'In {n}, how many tens are there?', f'{n} = ? tens and {n % 10} ones', near(r, n // 10, 3, 0, 2), 0, f'{p} {n} has {n // 10} tens.', f'{t} {n} = {n // 10} tens and {n % 10} ones.', 'place value')
        coins = [1, 5, 10, 20]; a, b = r.choice(coins), r.choice(coins)
        return item(f'You have {a} pesos and {b} pesos. How much in all?', f'₱{a} + ₱{b} = ?', ['₱' + str(x) for x in near(r, a + b, 3, 1, 5)], 0,
                    f'{p} ₱{a + b} in all.', f'{t} ₱{a} + ₱{b} = ₱{a + b}.', 'money')
    if level == 5:
        k = r.random()
        if k < .4:
            a, b = r.randint(2, 10), r.randint(2, 10)
            return item(f'{a} times {b}?', f'{a} × {b} = ?', near(r, a * b, 3, 0, max(2, a)), 0, f'{p} {a} × {b} = {a * b}.', f'{t} {a} groups of {b} is {a * b}.', 'multiplication')
        if k < .65:
            b, c = r.randint(2, 10), r.randint(2, 10)
            return item(f'{b * c} divided by {b}?', f'{b * c} ÷ {b} = ?', near(r, c, 3, 1, 2), 0, f'{p} {b * c} ÷ {b} = {c}.', f'{t} {b} × {c} = {b * c}, so the answer is {c}.', 'division')
        if k < .85:
            parts = r.choice([2, 3, 4])
            shaded = r.randint(1, parts - 1) if parts > 2 else 1
            return item('What part is colored?', '🟦' * shaded + '⬜' * (parts - shaded), [f'{shaded}/{parts}', f'{parts - shaded}/{parts}' if parts - shaded != shaded else f'{shaded}/{parts + 1}', f'{parts}/{shaded}'], 0,
                        f'{p} {shaded} out of {parts} parts: {shaded}/{parts}.', f'{t} {shaded} of {parts} equal parts are colored: {shaded}/{parts}.', 'fractions')
        n = r.randint(1, 9) * 10 + r.randint(1, 9)
        rd = int(round(n / 10.0) * 10) if n % 10 != 5 else n + 5
        return item(f'Round {n} to the nearest ten.', f'{n} ≈ ?', [str(rd), str(rd - 10 if rd > 10 else rd + 10), str(rd + 10 if rd - 10 > 0 else rd + 20)], 0,
                    f'{p} {n} rounds to {rd}.', f'{t} {n} is closer to {rd}.', 'rounding')
    k = r.random()
    if k < .3:
        a, b = r.randint(12, 49), r.randint(2, 9)
        return item(f'{a} times {b}?', f'{a} × {b} = ?', near(r, a * b, 3, 0, 10), 0, f'{p} {a * b}.', f'{t} {a} × {b} = {a * b}.', '2-digit multiplication')
    if k < .5:
        d = r.choice([2, 3, 4, 5, 6, 8]); a, b = r.randint(1, d - 1), r.randint(1, d - 1)
        while a + b > d:
            a, b = r.randint(1, d - 1), r.randint(1, d - 1)
        return item(f'{a} over {d} plus {b} over {d}?', f'{a}/{d} + {b}/{d} = ?', [f'{a + b}/{d}', f'{a + b}/{d * 2}', f'{a * b}/{d}' if a * b != a + b else f'{a + b + 1}/{d}'], 0,
                    f'{p} {a + b}/{d}.', f'{t} Same bottom number, add the tops: {a + b}/{d}.', 'adding fractions')
    if k < .7:
        l, w = r.randint(2, 12), r.randint(2, 9)
        return item(f'A rectangle is {l} by {w} centimeters. What is its perimeter?', f'▭  {l} cm × {w} cm', [f'{2 * (l + w)} cm', f'{l * w} cm', f'{l + w} cm'], 0,
                    f'{p} {l} + {w} + {l} + {w} = {2 * (l + w)} cm.', f'{t} Add all sides: {2 * (l + w)} cm.', 'perimeter')
    if k < .85:
        q, b = r.randint(10, 40), r.randint(2, 6)
        return item(f'{q * b} divided by {b}?', f'{q * b} ÷ {b} = ?', near(r, q, 3, 1, 3), 0, f'{p} {q}.', f'{t} {b} × {q} = {q * b}.', 'long division')
    price, n = r.choice([12, 15, 20, 25, 35]), r.randint(2, 5)
    return item(f'One pandesal pack costs {price} pesos. How much for {n} packs?', f'₱{price} × {n} = ?', ['₱' + str(x) for x in near(r, price * n, 3, 1, price)], 0,
                f'{p} ₱{price * n}.', f'{t} {price} × {n} = {price * n} pesos.', 'word problem')

def g_shapes(level, r):
    p, t = r.choice(PRAISE['en']), r.choice(TRY['en'])
    if level == 1:
        if r.random() < .5:
            name, em = r.choice(COLORS)
            return item(f'Which one is {name}?', 'Find ' + name, mc(r, em, [c[1] for c in COLORS]), 0, f'{p} That is {name}.', f'{t} This is {name}: {em}', 'colors')
        name, em = r.choice(SHAPES)
        return item(f'Which one is a {name}?', 'Find the ' + name, mc(r, em, [s[1] for s in SHAPES]), 0, f'{p} That is a {name}.', f'{t} This is a {name}: {em}', 'shapes')
    k = r.random()
    if k < .35:
        em, obj, col = r.choice(COLOR_OBJ)
        return item(f'What color is the {obj}?', em, mc(r, col, [c[0] for c in COLORS]), 0, f'{p} The {obj} is {col}.', f'{t} A {obj} is {col}.', 'colors')
    if k < .7:
        a, b = r.sample([c[1] for c in COLORS[:6]] + [s[1] for s in SHAPES[:4]], 2)
        return item('What comes next in the pattern?', f'{a} {b} {a} {b} {a} ___', mc(r, b, [c[1] for c in COLORS[:6]] + [s[1] for s in SHAPES[:4]]), 0,
                    f'{p} The pattern repeats.', f'{t} {a}, {b}, {a}, {b}... next is {b}.', 'patterns')
    name, n, em = r.choice(SIDES[:3])
    return item(f'How many sides does a {name} have?', em + ' ' + name, near(r, n, 3, 2, 2), 0, f'{p} A {name} has {n} sides.', f'{t} Count the sides: {n}.', 'sides')

def g_filipino(level, r):
    p, t = r.choice(PRAISE['fil']), r.choice(TRY['fil'])
    if level <= 2:
        w, pic = r.choice(FIL_WORDS)
        if r.random() < .5:
            first = w[0].upper()
            pool = list('ABDEGHIKLMNOPRSTUWY')
            return item(f'Ano ang unang titik ng {w}?', f'{pic} {w}', mc(r, first, pool), 0, f'{p} Ang {w} ay nagsisimula sa {first}.', f'{t} Ang {w} ay nagsisimula sa {first}.', 'unang titik')
        return item('Alin ang tamang salita para sa larawan?', pic, mc(r, w, [x[0] for x in FIL_WORDS]), 0, f'{p} Ito ay {w}.', f'{t} Ito ay {w}.', 'salita')
    if level == 3:
        k = r.random()
        if k < .35:
            em, obj, col = r.choice(FIL_COLOR_OBJ)
            return item(f'Anong kulay ang {obj}?', em, mc(r, col, [c[0] for c in FIL_COLORS] + ['kayumanggi']), 0, f'{p} {col} ang {obj}.', f'{t} Ang kulay ng {obj} ay {col}.', 'kulay')
        if k < .7:
            n = r.randint(1, 10); e = r.choice(COUNT_EMOJI)
            return item('Ilan ang nakikita mo?', e * n, mc(r, FIL_NUM[n - 1], FIL_NUM[max(0, n - 3):n + 2]), 0, f'{p} {FIL_NUM[n - 1]}!', f'{t} Bilangin natin: {FIL_NUM[n - 1]}.', 'bilang')
        a, b = r.choice(FIL_OPP[:6])
        return item(f'Ano ang kasalungat ng {a}?', a + ' ↔ ?', mc(r, b, [o[1] for o in FIL_OPP]), 0, f'{p} Ang kasalungat ng {a} ay {b}.', f'{t} Ang kasalungat ng {a} ay {b}.', 'kasalungat')
    if level == 4:
        k = r.random()
        if k < .35:
            a, b = r.choice(FIL_OPP)
            return item(f'Ano ang kasalungat ng {a}?', a + ' ↔ ?', mc(r, b, [o[1] for o in FIL_OPP]), 0, f'{p} {a} at {b}.', f'{t} Ang kasalungat ng {a} ay {b}.', 'kasalungat')
        if k < .7:
            v = r.choice(FIL_VERB)
            return item('Alin ang salitang kilos?', 'Salitang kilos (pandiwa)?', [v] + r.sample(FIL_NOUN, 2), 0, f'{p} Ang {v} ay pandiwa.', f'{t} Ang {v} ay kilos. Ito ay pandiwa.', 'pandiwa')
        n = r.choice(FIL_NOUN)
        return item('Alin ang pangngalan?', 'Pangngalan?', [n] + r.sample(FIL_VERB, 2), 0, f'{p} Ang {n} ay pangngalan.', f'{t} Ang {n} ay ngalan ng tao, bagay o lugar.', 'pangngalan')
    k = r.random()
    if k < .3:
        pas = [x for x in FIL_PASSAGES if x[0] <= level]
        _, text, qs = r.choice(pas)
        q, right, wrong = r.choice(qs)
        return item('Basahin at sagutin. ' + q, text + '\n\n' + q, [right] + wrong, 0, f'{p} {right}.', f'{t} Basahin muli. Ang sagot ay {right}.', 'pagbasa')
    if k < .55:
        a, b = r.choice(FIL_SYN)
        return item(f'Ano ang kasingkahulugan ng {a}?', a + ' = ?', mc(r, b, [s[1] for s in FIL_SYN]), 0, f'{p} {a} at {b}.', f'{t} Ang {a} ay {b}.', 'kasingkahulugan')
    if k < .8 or level == 5:
        adj = r.choice(FIL_ADJ)
        return item('Alin ang salitang naglalarawan?', 'Pang-uri?', [adj] + r.sample(FIL_VERB, 2), 0, f'{p} Ang {adj} ay pang-uri.', f'{t} Ang {adj} ay naglalarawan. Ito ay pang-uri.', 'pang-uri')
    root, past, now, fut = r.choice(FIL_ASPECT)
    when, ans = r.choice([('Kahapon', past), ('Ngayon', now), ('Bukas', fut)])
    return item(f'{when}, si Ana ay ... Alin ang tama?', f'{when}, si Ana ay ___. ({root})', [ans] + [x for x in (past, now, fut) if x != ans], 0,
                f'{p} {when}, si Ana ay {ans}.', f'{t} {when}, ang tamang salita ay {ans}.', 'aspekto ng pandiwa')

def g_science(level, r):
    p, t = r.choice(PRAISE['en']), r.choice(TRY['en'])
    em, q, right, wrong = r.choice(SCIENCE[clamp_level('science', level)])
    return item(q, em + '  ' + q, [right] + wrong, 0, f'{p} {right}.', f'{t} The answer is {right}.', 'science')

GEN = {'phonics': g_phonics, 'reading': g_reading, 'math': g_math, 'shapes': g_shapes, 'filipino': g_filipino, 'science': g_science}

def local_round(subject, level, n=5, seed=None):
    r = random.Random(seed)
    level = clamp_level(subject, level)
    items, seen = [], set()
    tries = 0
    while len(items) < n and tries < 60:
        tries += 1
        it = GEN[subject](level, r)
        key = it['text'] + '|' + it['say']
        if key in seen or len(set(it['choices'])) != len(it['choices']):
            continue
        seen.add(key)
        items.append(shuffle_item(it, r))
    lang = SUBJ[subject]['lang']
    intro = ("Hi {name}! Let's learn " + SUBJ[subject]['name'] + '. Tap the right answer.') if lang == 'en' else 'Kumusta, {name}! Mag-aral tayo. Pindutin ang tamang sagot.'
    return {'intro': intro, 'items': items, 'source': 'local', 'usage': None}

# ---------------------------------------------------------------- AI (Claude)
API_URL = 'https://api.anthropic.com/v1/messages'

SYSTEM_PROMPT = """You are {tutor}, a warm, patient tutor in a learning app for Filipino children from Preschool to Grade 4 (ages 3 to 10).
A parent is nearby. The child cannot type, so every question is answered by TAPPING one of 2 to 4 big buttons.
Your text is read aloud by a text-to-speech voice, so write short, clear, spoken sentences.

Return ONLY a JSON object, no other text, in exactly this shape:
{{"intro": "one short spoken greeting/instruction for this round",
  "items": [
    {{"say": "spoken question (max 25 words)",
      "text": "what is shown big on screen: emoji pictures, a word, a math problem, or a short reading passage (max 300 characters)",
      "choices": ["2 to 4 short options, max 30 characters each, emoji allowed"],
      "answer": 0,
      "correct": "short spoken praise that also restates the right answer",
      "wrong": "short, kind spoken hint that explains the right answer",
      "skill": "2-3 word skill tag"}}
  ]}}

Rules:
- Exactly {n} items. "answer" is the 0-based index of the ONE correct choice. All choices must be different, and only one may be correct.
- Match the level exactly. Level guide: Preschool = colors, shapes, letters, counting to 5; Kinder = letter sounds, counting to 10, adding within 5, simple words;
  Grade 1 = reading short sentences, rhymes, add/subtract within 20; Grade 2 = 2-digit add/subtract, place value, plurals, short stories;
  Grade 3 = multiplication/division facts, simple fractions, verbs/tenses, reading comprehension; Grade 4 = multi-digit multiplication, fractions, perimeter, grammar, longer passages.
- Use pictures as emoji in "text" or "choices" for young children (Preschool to Grade 1). Never refer to images other than emoji.
- Use Filipino everyday life: mango, jeepney, sari-sari store, pesos, fiesta, rice, typhoon, carabao. Keep everything kind, safe and age-appropriate.
- Never ask for personal information (name, address, school, family, photos). Never mention other apps, websites, links or the internet. Stay strictly on the lesson.
- Use the placeholder {{name}} (with curly braces) when you want to say the child's name, at most in the intro and a few praise lines.
- Mix question types inside the skill area so it stays fun. Include at least one item that practices a recent mistake, if any are given.
"""

def _lang_rule(subject, lang_pref):
    if SUBJ[subject]['lang'] == 'fil':
        return 'Write everything (say, text, choices, praise, hints) in simple Filipino (Tagalog).'
    if lang_pref == 'both':
        return 'Questions and choices in simple English. Praise and hints in English, but you may add a short Filipino word of encouragement (e.g. "Galing!").'
    return 'Write everything in simple English.'

def _clean(s, n):
    s = str(s or '').strip()
    s = re.sub(r'https?://\S+|www\.\S+', '', s)
    return s[:n]

def validate_round(data, n):
    if not isinstance(data, dict) or not isinstance(data.get('items'), list):
        raise ValueError('bad shape')
    items = []
    for it in data['items'][:n + 2]:
        try:
            choices = [_clean(c, 40) for c in it['choices']][:4]
            ans = int(it['answer'])
            if len(choices) < 2 or not (0 <= ans < len(choices)) or any(not c for c in choices) or len(set(choices)) != len(choices):
                continue
            x = {'say': _clean(it.get('say'), 220), 'text': _clean(it.get('text'), 400), 'choices': choices, 'answer': ans,
                 'correct': _clean(it.get('correct'), 200) or 'Great job!', 'wrong': _clean(it.get('wrong'), 220) or 'Good try!',
                 'skill': _clean(it.get('skill'), 40)}
            if not x['say'] and not x['text']:
                continue
            items.append(x)
        except (KeyError, TypeError, ValueError):
            continue
    if len(items) < max(3, n - 2):
        raise ValueError('too few valid items')
    return {'intro': _clean(data.get('intro'), 220) or "Let's learn!", 'items': items[:n]}

def ai_round(subject, level, age, lang_pref, mistakes, tutor_name, n=5, api_key=None, model=None, timeout=25):
    api_key = api_key or os.environ.get('ANTHROPIC_API_KEY', '')
    model = model or os.environ.get('ANTHROPIC_MODEL', 'claude-haiku-4-5-20251001')
    if not api_key:
        raise RuntimeError('no api key')
    level = clamp_level(subject, level)
    msg = [f'Subject: {SUBJ[subject]["name"]}', f'Level: {LEVELS[level - 1]}', f'Child age: {age}',
           _lang_rule(subject, lang_pref), f'Make {n} items.']
    if mistakes:
        msg.append('Recent mistakes to practice (question -> correct answer): ' + '; '.join(f'{m["prompt"]} -> {m["correct_answer"]}' for m in mistakes[:5]))
    body = {'model': model, 'max_tokens': 1800, 'temperature': 0.9,
            'system': SYSTEM_PROMPT.format(tutor=tutor_name, n=n),
            'messages': [{'role': 'user', 'content': '\n'.join(msg)}]}
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode(), method='POST', headers={
        'content-type': 'application/json', 'x-api-key': api_key, 'anthropic-version': '2023-06-01'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        out = json.loads(resp.read().decode())
    text = ''.join(b.get('text', '') for b in out.get('content', []) if b.get('type') == 'text')
    m = re.search(r'\{.*\}', text, re.S)
    if not m:
        raise ValueError('no json')
    rnd = validate_round(json.loads(m.group(0)), n)
    r = random.Random()
    rnd['items'] = [shuffle_item(i, r) for i in rnd['items']]
    rnd['source'] = 'ai'
    rnd['usage'] = out.get('usage') or {}
    return rnd

def make_round(subject, level, age, lang_pref='en', mistakes=None, tutor_name='Teacher Kiko', use_ai=True, n=5):
    if use_ai and os.environ.get('ANTHROPIC_API_KEY'):
        try:
            return ai_round(subject, level, age, lang_pref, mistakes or [], tutor_name, n)
        except Exception as e:  # network, quota, bad JSON -> built-in lessons
            print('AI round failed, using built-in lessons:', repr(e)[:200])
    return local_round(subject, level, n)
