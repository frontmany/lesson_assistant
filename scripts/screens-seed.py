"""Демо учитель, ученик и новичок для снимков стартовых экранов"""

from app.db import classes as db

TEACHER, STUDENT, NEWCOMER = 990001, 990101, 990201

if not any(c.name == "10А" for c, _ in db.classes_of(TEACHER)):
    a = db.create_class(TEACHER, "Марина Сергеевна", "10А", "история")
    b = db.create_class(TEACHER, "Марина Сергеевна", "9Б", "обществознание")
    db.create_class(TEACHER, "Марина Сергеевна", "8В", "история")
    for i, name in enumerate(["Иван Иванов", "Мария Смирнова", "Артём Котов", "Софья Ланская", "Алиса Новикова", "Дмитрий Петров"]):
        db.join(a.id, 990300 + i, name)
    for i, name in enumerate(["Егор Волков", "Злата Миронова", "Кирилл Белов"]):
        db.join(b.id, 990400 + i, name)
    db.join(a.id, STUDENT, "Пётр Новиков")
db.set_role(TEACHER, "teacher")
db.set_role(STUDENT, "student")
db.set_role(NEWCOMER, "none")
print([(c.name, c.subject, len(db.students_of(c.id))) for c, _ in db.classes_of(TEACHER)])
