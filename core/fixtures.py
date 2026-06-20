"""
Script de génération de données de test pour l'école de soutien
Usage: python manage.py shell < fixtures.py
OU créer une management command
"""
import random
from datetime import datetime, timedelta, time, date
from decimal import Decimal
from django.utils import timezone
from django.db import transaction
from django.db.models import Sum
from dateutil.relativedelta import relativedelta

# Importer les modèles
from .models import SessionException, Room, Teacher, CourseGroup, Student, Enrollment, Payment, Attendance, Session, Level


# ==================== DONNÉES DE BASE ====================

MOROCCAN_FIRST_NAMES = [
    # Garçons
    "Ahmed", "Mohamed", "Youssef", "Hassan", "Omar", "Karim", "Amine", "Mehdi",
    "Samir", "Rachid", "Abdelali", "Hamza", "Ismail", "Khalid", "Tariq",
    "Ayoub", "Zakaria", "Rayan", "Adam", "Ilyas",
    # Filles
    "Fatima", "Aicha", "Zineb", "Salma", "Hiba", "Meriem", "Khadija", "Nour",
    "Yasmine", "Safaa", "Laila", "Amina", "Siham", "Karima", "Houda",
    "Sanaa", "Rim", "Malak", "Imane", "Dounia"
]

MOROCCAN_LAST_NAMES = [
    "Alami", "Bennani", "El Amrani", "Filali", "Idrissi", "Benjelloun", "Tazi",
    "Lazrak", "Berrada", "Skalli", "Zahiri", "Kettani", "Chraibi", "Fassi",
    "Belhaj", "Sefrioui", "Oudghiri", "Cherkaoui", "Hassani", "Bensouda",
    "El Malki", "Kadiri", "Slaoui", "Benmoussa", "El Yousfi"
]

SUBJECTS = [
    "Mathématiques", "Physique-Chimie", "SVT", "Français", "Arabe",
    "Anglais", "Philosophie", "Histoire-Géo", "Économie", "Informatique"
]

LEVELS = [
    "1ère Bac Sciences", "2ème Bac Sciences", "1ère Bac Lettres", "2ème Bac Lettres",
    "Tronc Commun", "3ème Collège", "2ème Collège", "1ère Collège",
    "6ème Primaire", "5ème Primaire"
]

DAYS = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT']

PHONE_PREFIXES = ['0612', '0613', '0661', '0662', '0670', '0671', '0672', '0698', '0699']


# ==================== FONCTIONS UTILITAIRES ====================

def generate_phone():
    """Génère un numéro de téléphone marocain"""
    prefix = random.choice(PHONE_PREFIXES)
    number = ''.join([str(random.randint(0, 9)) for _ in range(6)])
    return f"{prefix}{number}"


def generate_full_name():
    """Génère un nom complet marocain"""
    first_name = random.choice(MOROCCAN_FIRST_NAMES)
    last_name = random.choice(MOROCCAN_LAST_NAMES)
    return f"{first_name} {last_name}"


def random_time(start_hour=8, end_hour=20):
    """Génère une heure aléatoire"""
    hour = random.randint(start_hour, end_hour - 1)
    minute = random.choice([0, 30])  # Seulement heures pleines ou demi-heures
    return time(hour, minute)


def random_date_in_range(start_date, end_date):
    """Génère une date aléatoire dans une plage"""
    days_diff = (end_date - start_date).days
    random_days = random.randint(0, days_diff)
    return start_date + timedelta(days=random_days)

def generate_sessions_for_courses(courses=None, days_past=30, days_future=14):
    """
    Generate sessions for courses using the utils function.
    """
    print("\n🕒 Génération des sessions (historique + prochains jours)...")
    
    if courses is None:
        courses = CourseGroup.objects.filter(is_active=True)
    
    today = timezone.now().date()
    sessions_start = today - timedelta(days=days_past)
    sessions_end = today + timedelta(days=days_future)
    
    from .utils import generate_sessions_from_coursegroups
    
    # Generate sessions for the range
    result = generate_sessions_from_coursegroups(sessions_start, sessions_end, force=True)
    
    # Update past sessions to DONE or CANCELLED to make it look realistic
    past_sessions = Session.objects.filter(date__lt=today, status='PLANNED')
    sessions_count = past_sessions.count()
    
    for s in past_sessions:
        s.status = 'DONE' if random.random() > 0.08 else 'CANCELLED'
        s.save()
        
    print(f"✅ Sessions générées et statuts de l'historique mis à jour")
    return sessions_count


# ==================== FONCTION PRINCIPALE ====================

@transaction.atomic
def generate_fixtures(
    num_rooms=6,
    num_teachers=8,
    num_courses=15,
    num_students=50,
    generate_payments=True,
    generate_attendance=True
):
    """
    Génère toutes les données de test
    
    Args:
        num_rooms: Nombre de salles (6 par défaut)
        num_teachers: Nombre de professeurs
        num_courses: Nombre de groupes de cours
        num_students: Nombre d'élèves
        generate_payments: Générer l'historique des paiements
        generate_attendance: Générer les présences
    """
    
    print("🔄 Suppression des anciennes données...")
    # Nettoyer les données existantes (dans l'ordre pour respecter les FK)
    Attendance.objects.all().delete()
    Payment.objects.all().delete()
    Session.objects.all().delete()
    Enrollment.objects.all().delete()
    Student.objects.all().delete()
    CourseGroup.objects.all().delete()
    Level.objects.all().delete()
    Teacher.objects.all().delete()
    Room.objects.all().delete()
    
    print("\n" + "="*50)
    print("🏫 GÉNÉRATION DES DONNÉES DE TEST")
    print("="*50 + "\n")
    
    # ==================== 1. SALLES ====================
    print(f"📍 Création de {num_rooms} salles...")
    rooms = []
    for i in range(1, num_rooms + 1):
        room = Room.objects.create(
            name=f"Salle {i}",
            capacity=random.randint(15, 30),
            is_active=True
        )
        rooms.append(room)
        print(f"   ✓ {room.name} - Capacité: {room.capacity}")

    # ==================== 1.5 NIVEAUX ====================
    print(f"\n📈 Création de {len(LEVELS)} niveaux...")
    db_levels = []
    for idx, lvl_name in enumerate(LEVELS):
        lvl = Level.objects.create(name=lvl_name, order=idx+1)
        db_levels.append(lvl)
        print(f"   ✓ {lvl.name}")
    
    # ==================== 2. PROFESSEURS ====================
    print(f"\n👨‍🏫 Création de {num_teachers} professeurs...")
    teachers = []
    for _ in range(num_teachers):
        method = random.choices(['PERCENTAGE', 'HOURLY'], weights=[0.7, 0.3])[0]
        percentage = Decimal(random.choice(['40.00', '50.00', '60.00']))
        rate = Decimal(random.choice(['80.00', '100.00', '120.00', '150.00']))
        
        teacher = Teacher.objects.create(
            name=generate_full_name(),
            phone=generate_phone(),
            email=f"{generate_full_name().lower().replace(' ', '.')}@email.com",
            hourly_rate=rate,
            payment_method=method,
            payment_percentage=percentage,
            is_active=True
        )
        teachers.append(teacher)
        if method == 'PERCENTAGE':
            print(f"   ✓ {teacher.name} - Part des gains: {teacher.payment_percentage}%")
        else:
            print(f"   ✓ {teacher.name} - Taux horaire: {teacher.hourly_rate} DH/h")
    
    # ==================== 3. GROUPES DE COURS ====================
    print(f"\n📚 Création de {num_courses} groupes de cours...")
    courses = []
    created_schedules = {}  # Pour éviter les conflits de salle
    from .models import CourseGroupSchedule
    
    for i in range(num_courses):
        subject = random.choice(SUBJECTS)
        level = random.choice(db_levels)
        teacher = random.choice(teachers)
        room = random.choice(rooms)
        day = random.choice(DAYS)
        
        # Essayer de trouver un créneau libre
        max_attempts = 20
        start_time = None
        end_time = None
        
        for attempt in range(max_attempts):
            start_time = random_time(8, 18)
            duration = random.choice([1.5, 2, 2.5])  # Durée en heures
            end_hour = start_time.hour + int(duration)
            end_minute = start_time.minute + int((duration % 1) * 60)
            if end_minute >= 60:
                end_hour += 1
                end_minute -= 60
            end_time = time(min(end_hour, 20), end_minute)
            
            # Vérifier conflit
            schedule_key = f"{room.id}_{day}_{start_time}"
            if schedule_key not in created_schedules:
                # Vérifier chevauchement avec d'autres cours de la même salle/jour
                has_conflict = False
                for existing_schedule in created_schedules.values():
                    if (existing_schedule['room'] == room and 
                        existing_schedule['day'] == day):
                        if (start_time < existing_schedule['end'] and 
                            end_time > existing_schedule['start']):
                            has_conflict = True
                            break
                
                if not has_conflict:
                    created_schedules[schedule_key] = {
                        'room': room,
                        'day': day,
                        'start': start_time,
                        'end': end_time
                    }
                    break
        
        if not start_time or not end_time:
            continue
            
        # Créer le cours
        price = Decimal(random.choice(['300.00', '400.00', '500.00', '600.00', '700.00']))
        
        try:
            course = CourseGroup.objects.create(
                name=f"{subject} - {level.name}",
                subject=subject,
                level=level,
                monthly_price=price,
                teacher=teacher,
                is_active=True
            )
            
            # Créer le premier horaire
            CourseGroupSchedule.objects.create(
                course_group=course,
                day=day,
                start_time=start_time,
                end_time=end_time,
                room=room
            )
            
            # 30% de chance d'avoir un second horaire par semaine
            if random.random() < 0.30:
                for second_day in DAYS:
                    if second_day == day:
                        continue
                    
                    second_start_time = None
                    second_end_time = None
                    for attempt in range(max_attempts):
                        second_start_time = random_time(8, 18)
                        second_duration = random.choice([1.5, 2])
                        second_end_hour = second_start_time.hour + int(second_duration)
                        second_end_minute = second_start_time.minute + int((second_duration % 1) * 60)
                        if second_end_minute >= 60:
                            second_end_hour += 1
                            second_end_minute -= 60
                        second_end_time = time(min(second_end_hour, 20), second_end_minute)
                        
                        second_key = f"{room.id}_{second_day}_{second_start_time}"
                        if second_key not in created_schedules:
                            has_conflict = False
                            for existing_schedule in created_schedules.values():
                                if (existing_schedule['room'] == room and 
                                    existing_schedule['day'] == second_day):
                                    if (second_start_time < existing_schedule['end'] and 
                                        second_end_time > existing_schedule['start']):
                                        has_conflict = True
                                        break
                            if not has_conflict:
                                created_schedules[second_key] = {
                                    'room': room,
                                    'day': second_day,
                                    'start': second_start_time,
                                    'end': second_end_time
                                }
                                break
                    
                    if second_start_time and second_end_time:
                        CourseGroupSchedule.objects.create(
                            course_group=course,
                            day=second_day,
                            start_time=second_start_time,
                            end_time=second_end_time,
                            room=room
                        )
                        break
            
            courses.append(course)
            print(f"   ✓ {course.name} - {price} DH")
        except Exception as e:
            print(f"   ⚠ Erreur création cours: {e}")
    
    # ==================== 4. ÉLÈVES ====================
    print(f"\n👨‍🎓 Création de {num_students} élèves...")
    students = []
    for _ in range(num_students):
        student = Student.objects.create(
            name=generate_full_name(),
            phone=generate_phone(),
            parent_contact=generate_phone(),
            parent_name=generate_full_name(),
            address=f"{random.randint(1, 200)} Rue {random.choice(['Hassan II', 'Mohamed V', 'Allal Ben Abdellah', 'Ibn Batouta'])}, Casablanca",
            date_of_birth=date(
                random.randint(2005, 2012),
                random.randint(1, 12),
                random.randint(1, 28)
            ),
            level=random.choice(db_levels),
            is_active=True
        )
        students.append(student)
        print(f"   ✓ {student.name}")
    
    # ==================== 5. INSCRIPTIONS ====================
    print(f"\n📝 Création des inscriptions...")
    enrollments_count = 0
    for student in students:
        # Chaque élève s'inscrit à 1-4 cours
        num_enrollments = random.randint(1, 4)
        student_courses = random.sample(courses, min(num_enrollments, len(courses)))
        
        for course in student_courses:
            # Date d'inscription entre il y a 6 mois et maintenant
            enrollment_date = random_date_in_range(
                timezone.now().date() - timedelta(days=180),
                timezone.now().date()
            )
            
            Enrollment.objects.create(
                student=student,
                course_group=course,
                enrolled_date=enrollment_date,
                is_active=True
            )
            enrollments_count += 1
        
        total_fees = student.total_monthly_fees()
        print(f"   ✓ {student.name} - {num_enrollments} cours - {total_fees} DH/mois")
    
    print(f"\n   Total: {enrollments_count} inscriptions créées")
    
    # ==================== 6. SESSIONS (planning historique) ====================
    courses
    generate_sessions_for_courses(courses=courses, days_past=30, days_future=14)
    
    # ==================== 6. PAIEMENTS ====================
    if generate_payments:
        print("\n💰 Génération de l'historique des paiements...")
        payments_count = 0

        base_month = timezone.now().date().replace(day=1)

        for month_offset in range(3, -1, -1):
            target_month = base_month - relativedelta(months=month_offset)

            print(f"\n   📅 Mois: {target_month.strftime('%B %Y')}")

            for student in students:
                total_fees = student.total_monthly_fees()
                if total_fees == 0:
                    continue

                scenario = random.choices(
                    ['full', 'partial', 'none'],
                    weights=[0.7, 0.2, 0.1]
                )[0]

                if scenario == 'none':
                    continue

                if scenario == 'full':
                    amount = total_fees
                else:
                    amount = (total_fees * Decimal(
                        random.choice(['0.5', '0.6', '0.7', '0.8'])
                    )).quantize(Decimal('0.01'))

                payment_date = target_month + timedelta(days=random.randint(0, 14))

                Payment.objects.create(
                    student=student,
                    amount=amount,
                    payment_date=payment_date,
                    month_covered=target_month,
                    status='PAID',
                    payment_method=random.choice(['CASH', 'TRANSFER', 'CHECK']),
                    notes="" if random.random() > 0.2 else "Paiement échelonné",
                    is_locked=month_offset >= 2
                )

                payments_count += 1
            
            print(f"      ✓ {payments_count} paiements créés pour ce mois")
        
        print(f"\n   Total: {payments_count} paiements créés")
    
    # ==================== 7. PRÉSENCES ====================
    if generate_attendance:
        print(f"\n✅ Génération des présences...")
        attendance_count = 0
        
        # Générer des présences pour les 30 derniers jours
        start_date = timezone.now().date() - timedelta(days=30)
        
        for single_date in (start_date + timedelta(n) for n in range(30)):
            day_of_week = single_date.weekday()  # 0=Lundi, 6=Dimanche
            
            # Mapper les jours de la semaine
            day_map = {
                0: 'MON', 1: 'TUE', 2: 'WED', 
                3: 'THU', 4: 'FRI', 5: 'SAT', 6: 'SUN'
            }
            day_code = day_map[day_of_week]
            
            # Trouver les cours de ce jour
            daily_courses = CourseGroup.objects.filter(schedules__day=day_code, is_active=True).distinct()
            
            for course in daily_courses:
                # Pour chaque élève inscrit
                enrolled_students = Student.objects.filter(
                    enrollments=course,
                    is_active=True
                )
                
                for student in enrolled_students:
                    # 90% de taux de présence
                    is_present = random.random() < 0.90
                    
                    Attendance.objects.create(
                        student=student,
                        course_group=course,
                        date=single_date,
                        is_present=is_present,
                        notes="" if is_present else random.choice([
                            "", "", "", "Malade", "Absent sans justification"
                        ])
                    )
                    attendance_count += 1
        
        print(f"   Total: {attendance_count} présences enregistrées")
    
    
    # ==================== RAPPORT FINAL ====================
    print("\n" + "="*50)
    print("✅ GÉNÉRATION TERMINÉE")
    print("="*50)
    print(f"\n📊 Résumé:")
    print(f"   • Salles: {Room.objects.count()}")
    print(f"   • Professeurs: {Teacher.objects.count()}")
    print(f"   • Groupes de cours: {CourseGroup.objects.count()}")
    print(f"   • Élèves: {Student.objects.count()}")
    print(f"   • Inscriptions: {Enrollment.objects.count()}")
    print(f"   • Paiements: {Payment.objects.count()}")
    print(f"   • Présences: {Attendance.objects.count()}")
    
    # Statistiques financières
    total_revenue = Payment.objects.filter(status='PAID').aggregate(
        total=Sum('amount')
    )['total'] or Decimal('0')
    
    print(f"\n💰 Recette totale: {total_revenue} DH")
    
    # Élèves impayés
    current_month = timezone.now().date().replace(day=1)
    unpaid_count = 0
    for student in Student.objects.filter(is_active=True):
        status = student.payment_status()
        if status in ['UNPAID', 'PARTIAL']:
            unpaid_count += 1
    
    print(f"⚠️  Élèves impayés/partiels ce mois: {unpaid_count}")
    
    print("\n" + "="*50)
    print("🎉 Vous pouvez maintenant tester l'application!")
    print("="*50 + "\n")


# ==================== FONCTION POUR MANAGEMENT COMMAND ====================

def run():
    """
    Fonction appelée si vous créez une management command
    """
    generate_fixtures(
        num_rooms=6,
        num_teachers=8,
        num_courses=15,
        num_students=50,
        generate_payments=True,
        generate_attendance=True
    )


# ==================== EXÉCUTION DIRECTE ====================

if __name__ == '__main__':
    # Pour utilisation via: python manage.py shell < fixtures.py
    print("⚠️  Utilisez plutôt: python manage.py shell")
    print("    Puis tapez: from app.fixtures import generate_fixtures; generate_fixtures()")


# ==================== VARIANTES RAPIDES ====================

def quick_test_data():
    """Version rapide avec peu de données (pour tests unitaires)"""
    generate_fixtures(
        num_rooms=3,
        num_teachers=3,
        num_courses=5,
        num_students=10,
        generate_payments=True,
        generate_attendance=False
    )


def full_test_data():
    """Version complète avec beaucoup de données (pour démo)"""
    generate_fixtures(
        num_rooms=6,
        num_teachers=12,
        num_courses=25,
        num_students=100,
        generate_payments=True,
        generate_attendance=True
    )