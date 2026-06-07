#!/usr/bin/env python3
"""Fix chip presets to include concrete child names + age (Skazik-style).

Skazik chips fill with "Сказка про Машу, 4 года, которая боится темноты..."
Lalaka chips were too generic ("A story about a 4-year-old child who..."). Fix.
"""
import json
from pathlib import Path

OUT = Path("/opt/skazka-bot/web/locales")

# Per-locale child-name examples + chip presets (5 chips × 13 locales).
# Pattern: "[Story-noun] about [Name], [age], who [trait]. Let them [outcome]."
PRESETS = {
    "en": {
        "dark":   "A story about Lily, 4 years old, who is afraid of the dark and won't fall asleep alone. Let her find a magical helper and learn it's safe.",
        "teeth":  "A story about Max, 5 years old, who hates brushing his teeth. Let a friendly tooth fairy show him what happens to neglected teeth.",
        "doctor": "A story about Mia, 3 years old, who is scared of going to the doctor. Let her befriend a kind animal-doctor and realise there's nothing to fear.",
        "garden": "A story about Leo, 4 years old, going to kindergarten for the first time and feeling anxious. Let him meet kind new friends and discover it can be fun.",
        "swim":   "A story about Emma, 5 years old, learning to swim and a little afraid of water. Let her meet an underwater friend who shows water can be your friend.",
    },
    "de": {
        "dark":   "Eine Geschichte über Mila, 4 Jahre alt, die Angst vor der Dunkelheit hat und nicht allein einschlafen will. Lass sie einen magischen Helfer finden.",
        "teeth":  "Eine Geschichte über Lukas, 5 Jahre alt, der seine Zähne nicht putzen will. Eine freundliche Zahnfee zeigt ihm, was mit vernachlässigten Zähnen passiert.",
        "doctor": "Eine Geschichte über Anna, 3 Jahre alt, die Angst vor dem Arztbesuch hat. Lass sie einen freundlichen Tierarzt kennenlernen und merken, dass es nichts zu fürchten gibt.",
        "garden": "Eine Geschichte über Felix, 4 Jahre alt, der zum ersten Mal in den Kindergarten geht und nervös ist. Lass ihn nette Freunde finden und Spaß entdecken.",
        "swim":   "Eine Geschichte über Lara, 5 Jahre alt, die schwimmen lernt und Angst vor Wasser hat. Lass sie einen Unterwasserfreund treffen, der ihr zeigt, dass Wasser ein Freund sein kann.",
    },
    "es": {
        "dark":   "Un cuento sobre Sofía, 4 años, que le teme a la oscuridad y no quiere dormir sola. Que encuentre un ayudante mágico y aprenda que está a salvo.",
        "teeth":  "Un cuento sobre Mateo, 5 años, que odia cepillarse los dientes. Que un hada amable le muestre qué pasa con los dientes descuidados.",
        "doctor": "Un cuento sobre Lucía, 3 años, que le teme al médico. Que se haga amiga de un doctor-animal amable y entienda que no hay nada que temer.",
        "garden": "Un cuento sobre Diego, 4 años, que va a la guardería por primera vez y está nervioso. Que haga amigos amables y descubra que es divertido.",
        "swim":   "Un cuento sobre Emma, 5 años, que aprende a nadar y le teme al agua. Que conozca a un amigo submarino que le enseñe que el agua puede ser su amiga.",
    },
    "fr": {
        "dark":   "Une histoire sur Lucie, 4 ans, qui a peur du noir et ne veut pas s'endormir seule. Qu'elle trouve un aide magique et apprenne qu'elle est en sécurité.",
        "teeth":  "Une histoire sur Hugo, 5 ans, qui déteste se brosser les dents. Qu'une gentille fée des dents lui montre ce qui arrive aux dents négligées.",
        "doctor": "Une histoire sur Léa, 3 ans, qui a peur d'aller chez le médecin. Qu'elle se lie d'amitié avec un gentil docteur-animal et comprenne qu'il n'y a rien à craindre.",
        "garden": "Une histoire sur Tom, 4 ans, qui va à la maternelle pour la première fois et est anxieux. Qu'il se fasse de gentils amis et découvre que c'est amusant.",
        "swim":   "Une histoire sur Chloé, 5 ans, qui apprend à nager et a un peu peur de l'eau. Qu'elle rencontre un ami sous-marin qui lui montre que l'eau peut être amie.",
    },
    "it": {
        "dark":   "Una storia su Sofia, 4 anni, che ha paura del buio e non vuole dormire da sola. Che trovi un aiutante magico e impari che è al sicuro.",
        "teeth":  "Una storia su Marco, 5 anni, che odia lavarsi i denti. Una fatina dei denti gentile gli mostri cosa succede ai denti trascurati.",
        "doctor": "Una storia su Giulia, 3 anni, che ha paura di andare dal dottore. Che diventi amica di un dottore-animale gentile e capisca che non c'è nulla da temere.",
        "garden": "Una storia su Luca, 4 anni, che va all'asilo per la prima volta ed è ansioso. Che trovi amici gentili e scopra che è divertente.",
        "swim":   "Una storia su Emma, 5 anni, che impara a nuotare e ha un po' paura dell'acqua. Che incontri un amico sottomarino che le mostri che l'acqua può essere amica.",
    },
    "pl": {
        "dark":   "Bajka o Mai, 4 lata, która boi się ciemności i nie chce zasypiać sama. Niech znajdzie magicznego pomocnika i nauczy się, że jest bezpiecznie.",
        "teeth":  "Bajka o Antku, 5 lat, który nienawidzi mycia zębów. Miła wróżka zębuszka pokaże mu, co dzieje się z zaniedbanymi zębami.",
        "doctor": "Bajka o Zosi, 3 lata, która boi się iść do lekarza. Niech zaprzyjaźni się z miłym doktorem-zwierzakiem i zrozumie, że nie ma się czego bać.",
        "garden": "Bajka o Kubie, 4 lata, który po raz pierwszy idzie do przedszkola i jest niespokojny. Niech znajdzie miłych przyjaciół i odkryje, że to świetna zabawa.",
        "swim":   "Bajka o Lence, 5 lat, która uczy się pływać i boi się wody. Niech spotka podwodnego przyjaciela, który pokaże, że woda też może być przyjacielem.",
    },
    "pt-BR": {
        "dark":   "Um conto sobre a Sofia, 4 anos, que tem medo do escuro e não dorme sozinha. Que ela encontre um ajudante mágico e aprenda que está segura.",
        "teeth":  "Um conto sobre o Davi, 5 anos, que odeia escovar os dentes. Que uma simpática fada dos dentes lhe mostre o que acontece com dentes descuidados.",
        "doctor": "Um conto sobre a Helena, 3 anos, com medo de ir ao médico. Que ela faça amizade com um doutor-bichinho gentil e perceba que não há nada a temer.",
        "garden": "Um conto sobre o Gabriel, 4 anos, indo pela primeira vez à creche e ansioso. Que faça novos amigos legais e descubra que é divertido.",
        "swim":   "Um conto sobre a Maria, 5 anos, aprendendo a nadar e com um pouco de medo da água. Que ela encontre um amigo submarino que mostre que a água também pode ser amiga.",
    },
    "tr": {
        "dark":   "4 yaşındaki Zeynep'in masalı. Karanlıktan korkuyor ve tek başına uyumak istemiyor. Sihirli bir yardımcı bulup güvende olduğunu öğrensin.",
        "teeth":  "5 yaşındaki Yusuf'un masalı. Diş fırçalamaktan nefret ediyor. Sevimli bir diş perisi, ihmal edilen dişlere ne olduğunu göstersin.",
        "doctor": "3 yaşındaki Elif'in masalı. Doktora gitmekten korkuyor. Sevecen bir hayvan-doktor ile dost olup korkacak bir şey olmadığını anlasın.",
        "garden": "4 yaşındaki Mert'in masalı. İlk kez anaokuluna gidiyor ve endişeli. İyi arkadaşlar edinip orada eğlenceli olduğunu keşfetsin.",
        "swim":   "5 yaşındaki Defne'nin masalı. Yüzme öğreniyor ve sudan biraz korkuyor. Su altından bir arkadaş tanıyıp suyun da dostluk olabileceğini keşfetsin.",
    },
    "ja": {
        "dark":   "暗いのが怖くて一人で眠れない4歳のユキの物語。魔法の助けを見つけて、安心できると気づきます。",
        "teeth":  "歯磨きが大嫌いな5歳のヒロシの物語。優しい歯の妖精が、磨かない歯がどうなるか教えてくれます。",
        "doctor": "病院に行くのが怖い3歳のサクラの物語。優しい動物のお医者さんと友達になり、怖がる必要がないと気づきます。",
        "garden": "初めて幼稚園に行く4歳のレンの物語。優しい友達ができて、楽しいと気づきます。",
        "swim":   "水泳を習い始めた5歳のミオの物語。水を少し怖がるが、海の友達と出会い、水も友達になれることを知ります。",
    },
    "ko": {
        "dark":   "어둠을 무서워해 혼자 잠들지 못하는 4살 소라의 이야기. 마법의 도우미를 만나 안전하다는 걸 알게 됩니다.",
        "teeth":  "양치질을 싫어하는 5살 민준의 이야기. 친절한 이의 요정이 방치된 이가 어떻게 되는지 보여줍니다.",
        "doctor": "병원 가는 것을 무서워하는 3살 지우의 이야기. 친절한 동물 의사 친구를 만나 두려워할 게 없다는 것을 깨닫습니다.",
        "garden": "처음 어린이집에 가는 4살 도윤의 이야기. 친절한 친구를 만나 즐겁다는 것을 알게 됩니다.",
        "swim":   "수영을 배우는 5살 하린의 이야기. 물을 조금 무서워하지만 바닷속 친구를 만나 물도 친구가 될 수 있다는 걸 알게 됩니다.",
    },
    "ar": {
        "dark":   "قصة عن ليلى، ٤ سنوات، تخاف من الظلام ولا تستطيع النوم وحدها. لتجد مساعدًا سحريًا وتتعلم أنها بأمان.",
        "teeth":  "قصة عن عمر، ٥ سنوات، يكره تنظيف أسنانه. لتأتي جنية الأسنان اللطيفة وتُريه ماذا يحدث للأسنان المهملة.",
        "doctor": "قصة عن مريم، ٣ سنوات، تخاف من زيارة الطبيب. لتصادق طبيبًا حيوانًا لطيفًا وتدرك أنه لا داعي للخوف.",
        "garden": "قصة عن يوسف، ٤ سنوات، يذهب إلى الروضة لأول مرة ويشعر بالقلق. ليجد أصدقاء طيبين ويكتشف أن الأمر ممتع.",
        "swim":   "قصة عن سارة، ٥ سنوات، تتعلم السباحة وتخاف قليلًا من الماء. لتلتقي بصديق تحت الماء يُريها أن الماء يمكن أن يكون صديقًا.",
    },
    "ru": {
        "dark":   "Сказка про Машу, 4 года, которая боится темноты и не хочет засыпать одна. Пусть найдёт волшебного помощника и научится с этим справляться.",
        "teeth":  "Сказка про Артёма, 5 лет, который не любит чистить зубы. Пусть встретит волшебную фею, которая покажет, что бывает с зубами без ухода.",
        "doctor": "Сказка про Сашу, 3 года, который боится идти к врачу. Пусть подружится с добрым доктором-зверем и поймёт, что бояться нечего.",
        "garden": "Сказка про Никиту, 4 года, который первый раз идёт в детский сад и переживает. Пусть найдёт добрых друзей и поймёт, что в саду интересно.",
        "swim":   "Сказка про Мишу, 5 лет, который учится плавать и пока боится воды. Пусть встретит подводного жителя, который научит дружить с водой.",
    },
    "uk": {
        "dark":   "Казка про Софію, 4 роки, яка боїться темряви і не хоче засинати сама. Хай знайде чарівного помічника і навчиться з цим справлятися.",
        "teeth":  "Казка про Максима, 5 років, який не любить чистити зуби. Хай зустріне чарівну фею, що покаже, що буває з зубами без догляду.",
        "doctor": "Казка про Олю, 3 роки, яка боїться йти до лікаря. Хай подружиться з добрим лікарем-звіром і зрозуміє, що боятися нема чого.",
        "garden": "Казка про Тараса, 4 роки, який вперше йде в дитсадок і хвилюється. Хай знайде добрих друзів і зрозуміє, що в садку цікаво.",
        "swim":   "Казка про Андрія, 5 років, який вчиться плавати і боїться води. Хай зустріне підводного жителя, який навчить дружити з водою.",
    },
}

KEY_MAP = {
    "dark":   "chip_dark_full",
    "teeth":  "chip_teeth_full",
    "doctor": "chip_doctor_full",
    "garden": "chip_garden_full",
    "swim":   "chip_swim_full",
}

for loc, presets in PRESETS.items():
    p = OUT / f"{loc}.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    changed = 0
    for short_key, preset_text in presets.items():
        full_key = KEY_MAP[short_key]
        old = data.get(full_key, "")
        if old != preset_text:
            data[full_key] = preset_text
            changed += 1
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  {loc}: {changed} chip presets updated")
