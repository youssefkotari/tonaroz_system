"""
Utilitaires pour le système de gestion d'école
"""
from .models import Session, CourseGroup, SessionException  # Import necessary models
from django.db.models import Sum
from django.utils import timezone
from django.conf import settings
from decimal import Decimal
from datetime import date
from typing import List, Dict, Tuple, Optional
import calendar
from io import BytesIO
from reportlab.lib.pagesizes import A5
from reportlab.pdfgen import canvas
from .models import Student, Payment

PAID_STATUSES = ('PAID', 'OK', 'CONFIRMED', 'COMPLETED', 'SETTLED')

class SafeDict(dict):
    def __missing__(self, key):
        return f"{{{key}}}"


# ==================== GESTION DES DATES ====================

def get_current_month_period() -> Tuple[date, date]:
    """Retourne le premier et dernier jour du mois en cours"""
    today = timezone.now().date()
    first_day = today.replace(day=1)
    last_day = date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
    return first_day, last_day


def get_month_period(year: int, month: int) -> Tuple[date, date]:
    """Retourne le premier et dernier jour d'un mois donné"""
    first_day = date(year, month, 1)
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    return first_day, last_day


def get_next_month(reference_date: date) -> date:
    """Retourne le premier jour du mois suivant"""
    if reference_date.month == 12:
        return date(reference_date.year + 1, 1, 1)
    return date(reference_date.year, reference_date.month + 1, 1)


def get_previous_month(reference_date: date) -> date:
    """Retourne le premier jour du mois précédent"""
    if reference_date.month == 1:
        return date(reference_date.year - 1, 12, 1)
    return date(reference_date.year, reference_date.month - 1, 1)


def month_name_fr(month_number: int) -> str:
    """Retourne le nom du mois en français"""
    months = {
        1: "Janvier", 2: "Février", 3: "Mars", 4: "Avril",
        5: "Mai", 6: "Juin", 7: "Juillet", 8: "Août",
        9: "Septembre", 10: "Octobre", 11: "Novembre", 12: "Décembre"
    }
    return months.get(month_number, "")


# ==================== CALCULS FINANCIERS ====================

def count_scheduled_sessions_in_month(group, year: int, month: int) -> int:
    """Determine the total scheduled sessions in that calendar month using CourseGroupSchedule"""
    schedules = group.schedules.all()
    if not schedules:
        return 0
    
    cal_weekday_map = {0: 'MON', 1: 'TUE', 2: 'WED', 3: 'THU', 4: 'FRI', 5: 'SAT', 6: 'SUN'}
    num_days = calendar.monthrange(year, month)[1]
    
    total_sessions = 0
    for day in range(1, num_days + 1):
        d = date(year, month, day)
        wday_code = cal_weekday_map[d.weekday()]
        total_sessions += sum(1 for s in schedules if s.day == wday_code)
        
    return total_sessions


def count_remaining_sessions_in_month(group, start_date: date) -> int:
    """Counts the remaining scheduled sessions in a month starting from start_date (inclusive)"""
    schedules = group.schedules.all()
    if not schedules:
        return 0
    
    year = start_date.year
    month = start_date.month
    num_days = calendar.monthrange(year, month)[1]
    
    cal_weekday_map = {0: 'MON', 1: 'TUE', 2: 'WED', 3: 'THU', 4: 'FRI', 5: 'SAT', 6: 'SUN'}
    
    remaining_sessions = 0
    for day in range(start_date.day, num_days + 1):
        d = date(year, month, day)
        wday_code = cal_weekday_map[d.weekday()]
        remaining_sessions += sum(1 for s in schedules if s.day == wday_code)
        
    return remaining_sessions


def calculate_enrollment_expected_fee(enrollment, month_date: date) -> Decimal:
    """Returns the expected monthly fee for an enrollment, pro-rated if registered mid-month"""
    group = enrollment.course_group
    # If enrolled date is in a future month compared to month_date, they owe nothing
    if enrollment.enrolled_date.year > month_date.year or (
        enrollment.enrolled_date.year == month_date.year and enrollment.enrolled_date.month > month_date.month
    ):
        return Decimal('0.00')

    # Check if enrolled date matches month_date
    if enrollment.enrolled_date.year == month_date.year and enrollment.enrolled_date.month == month_date.month:
        if enrollment.enrolled_date.day > 1:
            total_sessions = count_scheduled_sessions_in_month(group, month_date.year, month_date.month)
            if total_sessions == 0:
                return Decimal('0.00')
            session_price = (group.monthly_price / Decimal(total_sessions)).quantize(Decimal('0.01'))
            remaining_sessions = count_remaining_sessions_in_month(group, enrollment.enrolled_date)
            return (Decimal(remaining_sessions) * session_price).quantize(Decimal('0.01'))
    return group.monthly_price



def calculate_student_expected_fees_for_month(student, month_date: date) -> Decimal:
    """Calculates student's expected total fees for a given month, accounting for pro-rating"""
    active_enrollments = student.enrollment_set.filter(is_active=True).select_related('course_group')
    total = Decimal('0.00')
    for enrollment in active_enrollments:
        total += calculate_enrollment_expected_fee(enrollment, month_date)
    return total


def calculate_student_monthly_total(student) -> Decimal:
    """
    Calcule le total mensuel qu'un élève doit payer
    Basé sur ses inscriptions actives et les pro-rations éventuelles pour le mois en cours
    """
    current_month = timezone.now().date().replace(day=1)
    return calculate_student_expected_fees_for_month(student, current_month)


def get_student_payment_status(student, month_date: Optional[date] = None) -> Dict:
    """
    Retourne le statut de paiement détaillé d'un élève pour un mois
    
    Returns:
        {
            'required': Decimal,
            'paid': Decimal,
            'remaining': Decimal,
            'status': 'OK' | 'PARTIAL' | 'UNPAID',
            'percentage': float
        }
    """
    from .models import Payment
    
    if month_date is None:
        month_date = timezone.now().date().replace(day=1)
    
    required = calculate_student_expected_fees_for_month(student, month_date)
    
    paid = Payment.objects.filter(
        student=student,
        month_covered=month_date,
        status='PAID'
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    
    remaining = required - paid
    
    if required > 0:
        percentage = float((paid / required) * 100)
    else:
        percentage = 0.0
    
    if paid >= required:
        status = 'OK'
    elif paid > 0:
        status = 'PARTIAL'
    else:
        status = 'UNPAID'
    
    return {
        'required': required,
        'paid': paid,
        'remaining': remaining,
        'status': status,
        'percentage': percentage
    }



def get_daily_revenue(target_date: Optional[date] = None) -> Decimal:
    """Calcule la recette du jour"""
    from .models import Payment
    
    if target_date is None:
        target_date = timezone.now().date()
    
    revenue = Payment.objects.filter(
        payment_date=target_date,
        status='PAID'
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    
    return revenue


def get_monthly_revenue(year: int, month: int) -> Decimal:
    """Calcule la recette du mois"""
    from .models import Payment
    
    first_day, last_day = get_month_period(year, month)
    
    revenue = Payment.objects.filter(
        payment_date__range=[first_day, last_day],
        status='PAID'
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    
    return revenue


def get_unpaid_students(month_date: Optional[date] = None) -> List[dict]:
    """
    Retourne la liste des élèves actifs non à jour pour un mois donné
    """
    if month_date is None:
        month_date = timezone.now().date()
    month_date = month_date.replace(day=1)

    students = Student.objects.filter(is_active=True).prefetch_related('payments')

    unpaid_students = []

    for student in students:
        required = calculate_student_expected_fees_for_month(student, month_date)

        paid = Payment.objects.filter(
            student=student,
            month_covered=month_date,
            status__in=PAID_STATUSES
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')



        remaining = max(required - paid, Decimal('0'))

        if paid >= required and required > 0:
            status = 'OK'
        elif paid > 0:
            status = 'PARTIAL'
        else:
            status = 'UNPAID'

        if status in ['UNPAID', 'PARTIAL']:
            unpaid_students.append({
                'student': student,
                'required': required,
                'paid': paid,
                'remaining': remaining,
                'status': status,
            })

    return unpaid_students
# ==================== CALCULS PROFESSEURS ====================

def get_months_in_range(start_date: date, end_date: date) -> List[date]:
    """Retourne la liste des premiers jours des mois dans l'intervalle donné (inclusif)"""
    months = []
    current = start_date.replace(day=1)
    target_end = end_date.replace(day=1)
    while current <= target_end:
        months.append(current)
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return months


def calculate_class_gains(course_group, months: List[date]) -> Tuple[Decimal, Decimal, Decimal, Decimal]:
    """
    Calcule le chiffre d'affaires (gains) réel et théorique d'un groupe de cours
    pour une liste de mois donnés, ainsi que la part issue des inscriptions tardives.
    """
    from .models import Enrollment, Payment
    from django.db.models import Sum
    from collections import defaultdict

    active_enrollments = Enrollment.objects.filter(
        course_group=course_group,
        is_active=True
    ).select_related('student')
    students = [e.student for e in active_enrollments]

    if not students:
        return Decimal('0.00'), Decimal('0.00'), Decimal('0.00'), Decimal('0.00')

    payments = Payment.objects.filter(
        student__in=students,
        month_covered__in=months,
        status='PAID'
    )

    payment_map = defaultdict(lambda: defaultdict(Decimal))
    for p in payments:
        payment_map[p.student_id][p.month_covered] += p.amount

    gains_actual = Decimal('0.00')
    gains_theoretical = Decimal('0.00')
    gains_regular = Decimal('0.00')
    gains_late = Decimal('0.00')

    for enrollment in active_enrollments:
        student = enrollment.student
        for month in months:
            total_expected = calculate_student_expected_fees_for_month(student, month)
            course_expected = calculate_enrollment_expected_fee(enrollment, month)
            gains_theoretical += course_expected

            paid_amount = payment_map[student.id][month]
            if total_expected > 0:
                contribution = paid_amount * (course_expected / total_expected)
            else:
                contribution = Decimal('0.00')
            gains_actual += contribution
            
            # Differentiate late student additions (registered mid-month in this month)
            is_late = False
            if enrollment.enrolled_date.year == month.year and enrollment.enrolled_date.month == month.month:
                if enrollment.enrolled_date.day > 1:
                    is_late = True
            
            if is_late:
                gains_late += contribution
            else:
                gains_regular += contribution

    return (
        gains_actual.quantize(Decimal('0.01')),
        gains_theoretical.quantize(Decimal('0.01')),
        gains_regular.quantize(Decimal('0.01')),
        gains_late.quantize(Decimal('0.01'))
    )


def calculate_teacher_hours(teacher, start_date: date, end_date: date) -> Dict:
    """
    Calcule les heures travaillées et les gains/salaires pour un professeur sur une période
    """
    from .models import CourseGroup, Attendance, Session
    
    courses = CourseGroup.objects.filter(
        teacher=teacher,
        is_active=True
    )
    
    total_scheduled_hours = Decimal('0.00')
    total_taught_hours = Decimal('0.00')
    
    days_count = (end_date - start_date).days + 1
    weeks_count = Decimal(str(days_count)) / Decimal('7.0')
    
    for course in courses:
        weekly_hours = sum(Decimal(str(sch.duration_hours())) for sch in course.schedules.all())
        scheduled = weekly_hours * weeks_count
        total_scheduled_hours += scheduled
        
        done_sessions = Session.objects.filter(
            group=course,
            date__range=[start_date, end_date],
            status='DONE'
        )
        taught = sum(Decimal(str(s.duration_hours())) for s in done_sessions)
        total_taught_hours += taught
    
    if teacher.payment_method == 'PERCENTAGE':
        months = get_months_in_range(start_date, end_date)
        
        total_gains_actual = Decimal('0.00')
        total_gains_theoretical = Decimal('0.00')
        total_share_regular = Decimal('0.00')
        total_share_late = Decimal('0.00')
        courses_breakdown = []
        
        for course in courses:
            g_act, g_theo, reg_g, late_g = calculate_class_gains(course, months)
            total_gains_actual += g_act
            total_gains_theoretical += g_theo
            
            share_actual = (g_act * teacher.payment_percentage / Decimal('100.00')).quantize(Decimal('0.01'))
            share_theoretical = (g_theo * teacher.payment_percentage / Decimal('100.00')).quantize(Decimal('0.01'))
            share_regular = (reg_g * teacher.payment_percentage / Decimal('100.00')).quantize(Decimal('0.01'))
            share_late = (late_g * teacher.payment_percentage / Decimal('100.00')).quantize(Decimal('0.01'))
            
            total_share_regular += share_regular
            total_share_late += share_late
            
            courses_breakdown.append({
                'course': course,
                'student_count': course.students.filter(is_active=True).count(),
                'gains_actual': g_act,
                'gains_theoretical': g_theo,
                'share_actual': share_actual,
                'share_theoretical': share_theoretical,
                'share_regular': share_regular,
                'share_late': share_late,
                'gains_regular': reg_g,
                'gains_late': late_g,
            })
            
        salary_taught = (total_gains_actual * teacher.payment_percentage / Decimal('100.00')).quantize(Decimal('0.01'))
        salary_scheduled = (total_gains_theoretical * teacher.payment_percentage / Decimal('100.00')).quantize(Decimal('0.01'))
        
        return {
            'scheduled_hours': total_scheduled_hours,
            'taught_hours': total_taught_hours,
            'salary_scheduled': salary_scheduled,
            'salary_taught': salary_taught,
            'salary_regular': total_share_regular,
            'salary_late': total_share_late,
            'courses': courses.count(),
            'payment_method': 'PERCENTAGE',
            'payment_percentage': teacher.payment_percentage,
            'gains_actual': total_gains_actual,
            'gains_theoretical': total_gains_theoretical,
            'courses_breakdown': courses_breakdown,
            'months_covered': len(months),
        }
    elif teacher.payment_method == 'SESSION':
        total_scheduled_sessions = 0
        total_taught_sessions = 0
        
        for course in courses:
            done_sessions = Session.objects.filter(
                group=course,
                date__range=[start_date, end_date],
                status='DONE'
            )
            total_taught_sessions += done_sessions.count()
            
            scheduled_sessions_qs = Session.objects.filter(
                group=course,
                date__range=[start_date, end_date]
            ).exclude(status='CANCELLED')
            total_scheduled_sessions += scheduled_sessions_qs.count()
            
        session_rate = teacher.session_rate or Decimal('0.00')
        salary_taught = (Decimal(total_taught_sessions) * session_rate).quantize(Decimal('0.01'))
        salary_scheduled = (Decimal(total_scheduled_sessions) * session_rate).quantize(Decimal('0.01'))
        
        return {
            'scheduled_hours': total_scheduled_hours,
            'taught_hours': total_taught_hours,
            'scheduled_sessions': total_scheduled_sessions,
            'taught_sessions': total_taught_sessions,
            'salary_scheduled': salary_scheduled,
            'salary_taught': salary_taught,
            'courses': courses.count(),
            'payment_method': 'SESSION',
            'session_rate': session_rate,
        }
    else:
        salary_scheduled = (total_scheduled_hours * teacher.hourly_rate).quantize(Decimal('0.01'))
        salary_taught = (total_taught_hours * teacher.hourly_rate).quantize(Decimal('0.01'))
        
        return {
            'scheduled_hours': total_scheduled_hours,
            'taught_hours': total_taught_hours,
            'salary_scheduled': salary_scheduled,
            'salary_taught': salary_taught,
            'courses': courses.count(),
            'payment_method': 'HOURLY',
            'hourly_rate': teacher.hourly_rate,
        }


def generate_teacher_payslip_data(teacher, month: int, year: int) -> Dict:
    """
    Génère les données complètes pour une fiche de paie professeur
    """
    first_day, last_day = get_month_period(year, month)
    hours_data = calculate_teacher_hours(teacher, first_day, last_day)
    
    return {
        'teacher': teacher,
        'month': month_name_fr(month),
        'year': year,
        'period': f"{first_day.strftime('%d/%m/%Y')} - {last_day.strftime('%d/%m/%Y')}",
        'hourly_rate': teacher.hourly_rate if teacher.payment_method == 'HOURLY' else Decimal('0.00'),
        **hours_data
    }


# ==================== DÉTECTION DE CONFLITS ====================

def check_schedule_conflicts(room, schedule_day: str, start_time, end_time, exclude_schedule_id: Optional[int] = None) -> List:
    """
    Vérifie s'il y a des conflits d'horaire dans une salle pour les schedules
    
    Returns:
        Liste des horaires en conflit
    """
    from .models import CourseGroupSchedule
    
    conflicts = CourseGroupSchedule.objects.filter(
        room=room,
        day=schedule_day,
        course_group__is_active=True
    )
    
    if exclude_schedule_id:
        conflicts = conflicts.exclude(id=exclude_schedule_id)
    
    conflicting_schedules = []
    
    for sch in conflicts:
        # Vérifier chevauchement horaire
        if (start_time < sch.end_time and end_time > sch.start_time):
            conflicting_schedules.append(sch)
    
    return conflicting_schedules


def check_teacher_schedule_conflicts(teacher, schedule_day: str, start_time, end_time, exclude_schedule_id: Optional[int] = None) -> List:
    """
    Vérifie s'il y a des conflits d'horaire pour un professeur dans les schedules
    
    Returns:
        Liste des horaires en conflit
    """
    from .models import CourseGroupSchedule
    if not teacher:
        return []
        
    conflicts = CourseGroupSchedule.objects.filter(
        course_group__teacher=teacher,
        day=schedule_day,
        course_group__is_active=True
    )
    
    if exclude_schedule_id:
        conflicts = conflicts.exclude(id=exclude_schedule_id)
        
    conflicting_schedules = []
    
    for sch in conflicts:
        if (start_time < sch.end_time and end_time > sch.start_time):
            conflicting_schedules.append(sch)
            
    return conflicting_schedules


def detect_all_conflicts() -> Dict[str, List]:
    """
    Scans the database and returns all schedule, session, and capacity conflicts.
    """
    from .models import CourseGroupSchedule, Session, CourseGroup, Room
    
    schedule_conflicts = []
    session_conflicts = []
    capacity_warnings = []
    
    # 1. Weekly Schedule Conflicts (Room & Teacher)
    schedules = CourseGroupSchedule.objects.filter(course_group__is_active=True).select_related('course_group', 'course_group__teacher', 'room')
    processed_sch_pairs = set()
    
    for i, sch1 in enumerate(schedules):
        for sch2 in schedules[i+1:]:
            if sch1.id == sch2.id:
                continue
            # Check overlap if they are on the same day
            if sch1.day == sch2.day:
                if (sch1.start_time < sch2.end_time and sch1.end_time > sch2.start_time):
                    # Check Room conflict
                    if sch1.room == sch2.room:
                        pair_key = tuple(sorted([sch1.id, sch2.id]))
                        if pair_key not in processed_sch_pairs:
                            processed_sch_pairs.add(pair_key)
                            schedule_conflicts.append({
                                'type': 'ROOM',
                                'entity': sch1.room,
                                'sch1': sch1,
                                'sch2': sch2,
                                'description': f"La salle '{sch1.room.name}' est réservée en double le {sch1.get_day_display()} de {sch1.start_time.strftime('%H:%M')} à {sch1.end_time.strftime('%H:%M')}."
                            })
                    # Check Teacher conflict
                    if sch1.course_group.teacher == sch2.course_group.teacher:
                        pair_key = tuple(sorted([sch1.id, sch2.id]))
                        if pair_key not in processed_sch_pairs:
                            processed_sch_pairs.add(pair_key)
                            schedule_conflicts.append({
                                'type': 'TEACHER',
                                'entity': sch1.course_group.teacher,
                                'sch1': sch1,
                                'sch2': sch2,
                                'description': f"Le professeur '{sch1.course_group.teacher.name}' est affecté à deux cours le {sch1.get_day_display()} de {sch1.start_time.strftime('%H:%M')} à {sch1.end_time.strftime('%H:%M')}."
                            })

    # 2. Session Conflicts (Room & Teacher)
    today = timezone.now().date()
    # Load sessions and annotate conflicts/capacity in-memory using helper
    sessions_qs = Session.objects.filter(date__gte=today).exclude(status='CANCELLED').select_related('group', 'group__teacher', 'room').prefetch_related('group__students')
    annotated_sessions = _annotate_conflicts(sessions_qs)
    processed_sess_pairs = set()

    # Build session conflicts from the annotated in-memory list
    for i, s1 in enumerate(annotated_sessions):
        for s2 in annotated_sessions[i+1:]:
            if s1.id == s2.id:
                continue
            if s1.date == s2.date:
                if (s1.start_time < s2.end_time and s1.end_time > s2.start_time):
                    # Check Room conflict
                    if s1.room == s2.room:
                        pair_key = tuple(sorted([s1.id, s2.id]))
                        if pair_key not in processed_sess_pairs:
                            processed_sess_pairs.add(pair_key)
                            session_conflicts.append({
                                'type': 'ROOM',
                                'entity': s1.room,
                                'session1': s1,
                                'session2': s2,
                                'description': f"La salle '{s1.room.name}' est réservée en double le {s1.date.strftime('%d/%m/%Y')} de {s1.start_time.strftime('%H:%M')} à {s1.end_time.strftime('%H:%M')}."
                            })
                    # Check Teacher conflict
                    if s1.group.teacher == s2.group.teacher:
                        pair_key = tuple(sorted([s1.id, s2.id]))
                        if pair_key not in processed_sess_pairs:
                            processed_sess_pairs.add(pair_key)
                            session_conflicts.append({
                                'type': 'TEACHER',
                                'entity': s1.group.teacher,
                                'session1': s1,
                                'session2': s2,
                                'description': f"Le professeur '{s1.group.teacher.name}' est affecté à deux cours le {s1.date.strftime('%d/%m/%Y')} de {s1.start_time.strftime('%H:%M')} à {s1.end_time.strftime('%H:%M')}.",
                            })

    # 3. Capacity Warnings (enrolled count > room capacity)
    for sch in schedules:
        student_count = sch.course_group.students.filter(is_active=True).count()
        if student_count > sch.room.capacity:
            capacity_warnings.append({
                'context': 'SCHEDULE',
                'course': sch.course_group,
                'schedule': sch,
                'room': sch.room,
                'enrolled': student_count,
                'capacity': sch.room.capacity,
                'description': f"Le groupe '{sch.course_group.name}' compte {student_count} élèves inscrits, ce qui dépasse la capacité de la salle '{sch.room.name}' ({sch.room.capacity} places) le {sch.get_day_display()}."
            })
            
    # Use annotated sessions for capacity warnings (has_capacity_alert was set by _annotate_conflicts)
    for sess in annotated_sessions:
        if getattr(sess, 'has_capacity_alert', False):
            student_count = sess.group.students.filter(is_active=True).count()
            capacity_warnings.append({
                'context': 'SESSION',
                'session': sess,
                'course': sess.group,
                'room': sess.room,
                'enrolled': student_count,
                'capacity': sess.room.capacity,
                'description': f"La session du {sess.date.strftime('%d/%m/%Y')} pour '{sess.group.name}' compte {student_count} élèves inscrits, ce qui dépasse la capacité de la salle '{sess.room.name}' ({sess.room.capacity} places)."
            })

    return {
        'schedule_conflicts': schedule_conflicts,
        'session_conflicts': session_conflicts,
        'capacity_warnings': capacity_warnings,
        'total_count': len(schedule_conflicts) + len(session_conflicts) + len(capacity_warnings)
    }



def get_room_availability(room, target_day: str) -> List[Dict]:
    """
    Retourne les créneaux occupés d'une salle pour un jour donné
    """
    from .models import CourseGroupSchedule
    
    occupied_schedules = CourseGroupSchedule.objects.filter(
        room=room,
        day=target_day,
        course_group__is_active=True
    ).order_by('start_time')
    
    availability = []
    
    for sch in occupied_schedules:
        availability.append({
            'start': sch.start_time.strftime('%H:%M'),
            'end': sch.end_time.strftime('%H:%M'),
            'available': False,
            'course': sch.course_group
        })
    
    return availability


def generate_sessions_from_coursegroups(start_date: date, end_date: date, force: bool = False, course: Optional[CourseGroup] = None) -> Dict:
    """Create/update/delete Session objects based on CourseGroup schedules and per-date exceptions.

    Args:
        start_date: inclusive start date
        end_date: inclusive end date
        force: if True, update existing sessions when times/room differ
        course: optional specific CourseGroup to generate/sync sessions for

    Returns a summary dict: {'created', 'updated', 'deleted', 'skipped', 'errors'}
    """
    from .models import CourseGroup, Session, SessionException, CourseGroupSchedule
    from datetime import timedelta
    from django.core.exceptions import ValidationError

    DAY_MAP = {
        'MON': 0, 'TUE': 1, 'WED': 2, 'THU': 3,
        'FRI': 4, 'SAT': 5, 'SUN': 6
    }

    summary = {'created': 0, 'updated': 0, 'deleted': 0, 'skipped': 0, 'errors': []}

    # Clean up sessions for inactive groups
    if course:
        courses_to_clean = [course] if not course.is_active else []
    else:
        courses_to_clean = CourseGroup.objects.filter(is_active=False)

    for c in courses_to_clean:
        to_delete = Session.objects.filter(
            group=c,
            date__range=[start_date, end_date],
            status='PLANNED',
            is_manually_edited=False
        )
        for s in to_delete:
            try:
                s.delete()
                summary['deleted'] += 1
            except Exception as e:
                summary['errors'].append(f"Failed to delete session for inactive group {c.name} on {s.date}: {e}")

    # Determine which active courses to generate sessions for
    if course:
        courses = [course] if course.is_active else []
    else:
        courses = CourseGroup.objects.filter(is_active=True).prefetch_related('schedules', 'teacher')

    for active_course in courses:
        # Get active schedules for this course
        schedules = active_course.schedules.all()
        
        # 1. Clean up sessions that don't match any schedule slot (orphaned sessions)
        active_sessions = Session.objects.filter(
            group=active_course,
            date__range=[start_date, end_date],
            status='PLANNED',
            is_manually_edited=False
        )
        
        for s in active_sessions:
            matching_schedule = None
            for sch in schedules:
                if s.schedule == sch:
                    matching_schedule = sch
                    break
                # Fallback matching by weekday and time
                if s.date.weekday() == DAY_MAP.get(sch.day) and s.start_time == sch.start_time and s.end_time == sch.end_time:
                    if not s.schedule:
                        s.schedule = sch
                        s.save()
                    matching_schedule = sch
                    break
            
            if not matching_schedule:
                try:
                    s.delete()
                    summary['deleted'] += 1
                except Exception as e:
                    summary['errors'].append(f"Failed to delete orphaned session for {active_course.name} on {s.date}: {e}")

        # 2. Generate/update sessions for each schedule slot
        for sch in schedules:
            target_weekday = DAY_MAP.get(sch.day)
            if target_weekday is None:
                continue

            # first date in range matching the schedule's weekday
            days_ahead = (target_weekday - start_date.weekday()) % 7
            current = start_date + timedelta(days=days_ahead)

            while current <= end_date:
                try:
                    exception = SessionException.objects.filter(course_group=active_course, date=current).first()
                except Exception:
                    exception = None

                # cancelled exception -> delete existing session if present
                if exception and exception.cancelled:
                    existing = Session.objects.filter(group=active_course, date=current, schedule=sch).first()
                    if not existing:
                        existing = Session.objects.filter(group=active_course, date=current, start_time=sch.start_time, end_time=sch.end_time).first()
                    
                    if existing:
                        if existing.is_manually_edited or existing.status in ['DONE', 'CANCELLED']:
                            summary['skipped'] += 1
                        else:
                            try:
                                existing.delete()
                                summary['deleted'] += 1
                            except Exception as e:
                                summary['errors'].append(str(e))
                    else:
                        summary['skipped'] += 1
                    current += timedelta(days=7)
                    continue

                # determine effective values
                eff_room = exception.effective_room(sch.room) if exception else sch.room
                eff_start = exception.effective_start(sch.start_time) if exception else sch.start_time
                eff_end = exception.effective_end(sch.end_time) if exception else sch.end_time

                existing = Session.objects.filter(group=active_course, date=current, schedule=sch).first()
                if not existing:
                    existing = Session.objects.filter(group=active_course, date=current, start_time=sch.start_time, end_time=sch.end_time).first()
                    if existing and not existing.schedule:
                        existing.schedule = sch
                        existing.save()

                if existing:
                    if existing.is_manually_edited or existing.status in ['DONE', 'CANCELLED']:
                        summary['skipped'] += 1
                        current += timedelta(days=7)
                        continue

                    needs_update = (
                        existing.start_time != eff_start or
                        existing.end_time != eff_end or
                        existing.room != eff_room
                    )
                    if needs_update and force:
                        existing.start_time = eff_start
                        existing.end_time = eff_end
                        existing.room = eff_room
                        try:
                            existing.save()
                            summary['updated'] += 1
                        except ValidationError as ve:
                            summary['errors'].append(f"{active_course.name} {current}: {ve}")
                    else:
                        summary['skipped'] += 1
                else:
                    # create
                    try:
                        new = Session(
                            group=active_course,
                            schedule=sch,
                            date=current,
                            start_time=eff_start,
                            end_time=eff_end,
                            room=eff_room
                        )
                        new.save()
                        summary['created'] += 1
                    except ValidationError as ve:
                        summary['errors'].append(f"{active_course.name} {current}: {ve}")
                    except Exception as e:
                        summary['errors'].append(f"{active_course.name} {current}: {e}")

                current += timedelta(days=7)

    return summary



# ==================== GÉNÉRATION DE STATISTIQUES ====================

def get_dashboard_stats() -> Dict:
    """
    Génère toutes les statistiques pour le dashboard principal
    """
    from .models import Student, Teacher, CourseGroup, Payment, Room, CourseGroupSchedule
    
    today = timezone.now().date()
    current_month = today.replace(day=1)
    
    # Statistiques générales
    active_students = Student.objects.filter(is_active=True).count()
    active_teachers = Teacher.objects.filter(is_active=True).count()
    active_courses = CourseGroup.objects.filter(is_active=True).count()
    active_rooms = Room.objects.filter(is_active=True).count()
    
    # Statistiques financières
    today_revenue = get_daily_revenue(today)
    month_revenue = get_monthly_revenue(today.year, today.month)
    
    # Élèves impayés
    unpaid = get_unpaid_students(current_month)
    unpaid_count = len(unpaid)
    unpaid_amount = sum([u['remaining'] for u in unpaid])
    
    # Conflits de planning
    conflicts = []
    for sch in CourseGroupSchedule.objects.filter(course_group__is_active=True):
        sch_conflicts = check_schedule_conflicts(
            sch.room,
            sch.day,
            sch.start_time,
            sch.end_time,
            sch.id
        )
        if sch_conflicts:
            conflicts.append({
                'course': sch.course_group,
                'schedule': sch,
                'conflicts_with': sch_conflicts
            })
    
    return {
        'counts': {
            'students': active_students,
            'teachers': active_teachers,
            'courses': active_courses,
            'rooms': active_rooms
        },
        'revenue': {
            'today': today_revenue,
            'month': month_revenue,
        },
        'alerts': {
            'unpaid_count': unpaid_count,
            'unpaid_amount': unpaid_amount,
            'conflicts': conflicts,
            'unpaid_students': unpaid
        }
    }


# ==================== GÉNÉRATION DE REÇUS PDF ====================

def generate_receipt_pdf(payment) -> BytesIO:
    """
    Génère un reçu de paiement en format PDF (A5 ou thermique)
    """
    buffer = BytesIO()
    
    # Créer le PDF en format A5 (148 x 210 mm)
    p = canvas.Canvas(buffer, pagesize=A5)
    width, height = A5
    
    # En-tête
    p.setFont("Helvetica-Bold", 16)
    p.drawCentredString(width/2, height - 30, "REÇU DE PAIEMENT")
    
    # Numéro de reçu
    p.setFont("Helvetica", 10)
    p.drawString(30, height - 60, f"Reçu N° : {payment.receipt_number}")
    p.drawString(30, height - 75, f"Date : {payment.payment_date.strftime('%d/%m/%Y')}")
    
    # Ligne séparatrice
    p.line(30, height - 85, width - 30, height - 85)
    
    # Informations élève
    y_position = height - 110
    p.setFont("Helvetica-Bold", 11)
    p.drawString(30, y_position, "ÉLÈVE :")
    
    p.setFont("Helvetica", 10)
    y_position -= 20
    p.drawString(40, y_position, f"Nom : {payment.student.name}")
    y_position -= 15
    p.drawString(40, y_position, f"Contact Parent : {payment.student.parent_contact}")
    
    # Ligne séparatrice
    y_position -= 10
    p.line(30, y_position, width - 30, y_position)
    
    # Détails du paiement
    y_position -= 25
    p.setFont("Helvetica-Bold", 11)
    p.drawString(30, y_position, "DÉTAILS DU PAIEMENT :")
    
    p.setFont("Helvetica", 10)
    y_position -= 20
    p.drawString(40, y_position, f"Mois couvert : {payment.month_covered.strftime('%B %Y')}")
    y_position -= 15
    p.drawString(40, y_position, f"Mode de paiement : {payment.get_payment_method_display()}")
    
    # Montant (en gros)
    y_position -= 30
    p.setFont("Helvetica-Bold", 14)
    p.drawString(30, y_position, "MONTANT PAYÉ :")
    p.setFont("Helvetica-Bold", 18)
    p.drawString(width - 150, y_position, f"{payment.amount} DH")
    
    # Ligne séparatrice
    y_position -= 15
    p.line(30, y_position, width - 30, y_position)
    
    # Groupes inscrits
    y_position -= 25
    p.setFont("Helvetica-Bold", 10)
    p.drawString(30, y_position, "Groupes inscrits :")
    
    p.setFont("Helvetica", 9)
    enrollments = payment.student.enrollment_set.filter(is_active=True)
    month_covered = payment.month_covered
    for enrollment in enrollments[:5]:  # Max 5 pour ne pas déborder
        y_position -= 12
        is_prorated = False
        if month_covered and enrollment.enrolled_date.year == month_covered.year and enrollment.enrolled_date.month == month_covered.month:
            if enrollment.enrolled_date.day > 1:
                is_prorated = True
                
        if is_prorated:
            total_sess = count_scheduled_sessions_in_month(enrollment.course_group, month_covered.year, month_covered.month)
            rem_sess = count_remaining_sessions_in_month(enrollment.course_group, enrollment.enrolled_date)
            if total_sess > 0:
                sess_price = (enrollment.course_group.monthly_price / Decimal(total_sess)).quantize(Decimal('0.01'))
                prorated_price = (Decimal(rem_sess) * sess_price).quantize(Decimal('0.01'))
            else:
                sess_price = Decimal('0.00')
                prorated_price = Decimal('0.00')
            p.drawString(40, y_position, f"• {enrollment.course_group.name} (Proratisé: {rem_sess} sess. à {sess_price} DH) - {prorated_price} DH")
        else:
            p.drawString(40, y_position, f"• {enrollment.course_group.name} - {enrollment.course_group.monthly_price} DH")
    
    # Pied de page
    p.setFont("Helvetica-Oblique", 8)
    p.drawCentredString(width/2, 40, "Merci pour votre confiance")
    p.drawCentredString(width/2, 28, f"Centre Tonaroz - Soft Skills, Langues & Communication")
    
    # Finaliser
    p.showPage()
    p.save()
    
    buffer.seek(0)
    return buffer


def generate_thermal_receipt(payment) -> str:
    """
    Génère un reçu format texte pour imprimante thermique (58mm)
    Format simple pour WhatsApp ou impression ticket
    """
    receipt = f"""
{'='*32}
   REÇU DE PAIEMENT
{'='*32}
Reçu N° : {payment.receipt_number}
Date    : {payment.payment_date.strftime('%d/%m/%Y %H:%M')}
{'='*32}

ÉLÈVE : {payment.student.name}
Parent: {payment.student.parent_contact}

{'='*32}
Mois   : {payment.month_covered.strftime('%B %Y')}
Mode   : {payment.get_payment_method_display()}

{'='*32}
MONTANT : {payment.amount} DH
{'='*32}

Groupes inscrits :
"""
    
    enrollments = payment.student.enrollment_set.filter(is_active=True)
    for enrollment in enrollments:
        receipt += f"• {enrollment.course_group.name}\n"
        receipt += f"  {enrollment.course_group.monthly_price} DH/mois\n"
    
    receipt += f"""
{'='*32}
Merci pour votre confiance!
{'='*32}
"""
    
    return receipt


# ==================== NOTIFICATIONS ====================

def send_payment_reminder_sms(student, amount: Decimal) -> bool:
    """
    Envoie un SMS de rappel de paiement (à intégrer avec API SMS)
    """
    message = f"""
Bonjour,
Rappel : Un montant de {amount} DH reste à régler pour {student.name}.
Centre Tonaroz
    """.strip()
    
    # TODO: Intégrer avec une API SMS (Twilio, etc.)
    print(f"SMS envoyé à {student.parent_contact}: {message}")
    
    return True


def generate_whatsapp_link(phone: str, receipt_text: str) -> str:
    """
    Génère un lien WhatsApp Web avec le reçu pré-rempli
    """
    import urllib.parse
    
    # Nettoyer le numéro (enlever espaces, tirets)
    clean_phone = phone.replace(' ', '').replace('-', '').replace('+', '')
    
    # Encoder le message
    encoded_text = urllib.parse.quote(receipt_text)
    
    # Générer le lien
    whatsapp_link = f"https://wa.me/{clean_phone}?text={encoded_text}"
    
    return whatsapp_link


# ==================== VALIDATION ====================

def validate_payment_amount(student, amount: Decimal, month_date: date) -> Dict:
    """
    Valide qu'un montant de paiement est cohérent
    
    Returns:
        {'valid': bool, 'message': str, 'suggestion': Decimal}
    """
    required = calculate_student_monthly_total(student)
    status = get_student_payment_status(student, month_date)
    
    if amount <= 0:
        return {
            'valid': False,
            'message': "Le montant doit être supérieur à 0",
            'suggestion': required
        }
    
    if amount > (status['remaining'] * Decimal('1.5')):  # 50% de marge
        return {
            'valid': False,
            'message': f"Le montant semble trop élevé. Reste à payer : {status['remaining']} DH",
            'suggestion': status['remaining']
        }
    
    return {
        'valid': True,
        'message': "Montant valide",
        'suggestion': required
    }


# ==================== SESSIONS ====================


def _annotate_conflicts(sessions_qs):
    """
    Evaluates QuerySet and annotates session objects with conflicts & capacity alerts in-memory.
    """
    sessions_list = list(sessions_qs)
    
    for s in sessions_list:
        s.has_conflict = False
        s.conflict_message = ""
        s.has_capacity_alert = False
        
        # capacity check
        student_count = s.group.students.filter(is_active=True).count()
        if student_count > s.room.capacity:
            s.has_capacity_alert = True
            
    # Check room and teacher overlaps
    for i, s1 in enumerate(sessions_list):
        if s1.status == 'CANCELLED':
            continue
        for s2 in sessions_list[i+1:]:
            if s2.status == 'CANCELLED':
                continue
            if s1.date == s2.date and s1.id != s2.id:
                if (s1.start_time < s2.end_time and s1.end_time > s2.start_time):
                    # Room overlap
                    if s1.room_id == s2.room_id:
                        s1.has_conflict = True
                        s2.has_conflict = True
                        s1.conflict_message = f"Conflit de salle avec {s2.group.name}"
                        s2.conflict_message = f"Conflit de salle avec {s1.group.name}"
                    # Teacher overlap
                    if s1.group.teacher_id == s2.group.teacher_id:
                        s1.has_conflict = True
                        s2.has_conflict = True
                        s1.conflict_message = f"Conflit de professeur avec {s2.group.name}"
                        s2.conflict_message = f"Conflit de professeur avec {s1.group.name}"
                        
    return sessions_list


def _build_room_schedule(rooms, dates, sessions_list):
    """Build schedule rows organized by room from in-memory list"""
    rows = []
    
    for room in rooms:
        cells = []
        for date in dates:
            # Filter in memory
            day_sessions = [
                s for s in sessions_list 
                if s.room_id == room.id and s.date == date
            ]
            day_sessions.sort(key=lambda x: x.start_time)
            
            cells.append({
                'date': date,
                'sessions': day_sessions,
                'count': len(day_sessions)
            })
        
        if any(cell['count'] > 0 for cell in cells):
            rows.append({
                'entity': room,
                'entity_name': room.name,
                'entity_detail': f"{room.capacity} places",
                'cells': cells,
                'total_sessions': sum(cell['count'] for cell in cells)
            })
    
    return rows


def _build_teacher_schedule(teachers, dates, sessions_list):
    """Build schedule rows organized by teacher from in-memory list"""
    rows = []
    
    for teacher in teachers:
        cells = []
        for date in dates:
            # Filter in memory
            day_sessions = [
                s for s in sessions_list 
                if s.group.teacher_id == teacher.id and s.date == date
            ]
            day_sessions.sort(key=lambda x: x.start_time)
            
            cells.append({
                'date': date,
                'sessions': day_sessions,
                'count': len(day_sessions)
            })
        
        if any(cell['count'] > 0 for cell in cells):
            rows.append({
                'entity': teacher,
                'entity_name': teacher.name,
                'entity_detail': f"{teacher.payment_percentage}%" if teacher.payment_method == 'PERCENTAGE' else f"{teacher.hourly_rate} DH/h",
                'cells': cells,
                'total_sessions': sum(cell['count'] for cell in cells)
            })
    
    return rows


def _calculate_week_stats(sessions_list, dates):
    """Calculate statistics for the week using in-memory list"""
    total = len(sessions_list)
    planned = sum(1 for s in sessions_list if s.status == 'PLANNED')
    done = sum(1 for s in sessions_list if s.status == 'DONE')
    cancelled = sum(1 for s in sessions_list if s.status == 'CANCELLED')
    
    by_day = []
    for date in dates:
        day_sessions = [s for s in sessions_list if s.date == date]
        by_day.append({
            'date': date,
            'total': len(day_sessions),
            'planned': sum(1 for s in day_sessions if s.status == 'PLANNED'),
            'done': sum(1 for s in day_sessions if s.status == 'DONE'),
            'cancelled': sum(1 for s in day_sessions if s.status == 'CANCELLED'),
        })
    
    return {
        'total': total,
        'planned': planned,
        'done': done,
        'cancelled': cancelled,
        'by_day': by_day
    }



"""
WhatsApp Click-to-Chat Automation Utilities
============================================
Utilities for generating WhatsApp links and automating messaging.
"""

import urllib.parse
from typing import Optional, Dict, List
import re


class WhatsAppUtils:
    """Utility class for WhatsApp Click-to-Chat automation."""
    
    BASE_URL = "https://wa.me/"
    WEB_URL = "https://web.whatsapp.com/send"
    
    @staticmethod
    def clean_phone_number(phone: str) -> str:
        """
        Clean and format phone number for WhatsApp.
        
        Args:
            phone: Phone number in any format
            
        Returns:
            Cleaned phone number with only digits
            
        Example:
            >>> WhatsAppUtils.clean_phone_number("+212 6 12 34 56 78")
            '212612345678'
        """
        # Remove all non-digit characters
        cleaned = re.sub(r'\D', '', phone)
        
        # Remove leading zeros
        cleaned = cleaned.lstrip('0')
        
        return cleaned
    
    @staticmethod
    def generate_chat_link(
        phone: str,
        message: Optional[str] = None,
        use_web: bool = False
    ) -> str:
        """
        Generate WhatsApp click-to-chat link.
        
        Args:
            phone: Phone number with country code
            message: Pre-filled message (optional)
            use_web: Use WhatsApp Web instead of mobile (default: False)
            
        Returns:
            Complete WhatsApp URL
            
        Example:
            >>> WhatsAppUtils.generate_chat_link(
            ...     "+212612345678",
            ...     "Hello, I'm interested in your services"
            ... )
            'https://wa.me/212612345678?text=Hello%2C%20I%27m%20interested...'
        """
        cleaned_phone = WhatsAppUtils.clean_phone_number(phone)
        
        # Choose base URL
        base_url = WhatsAppUtils.WEB_URL if use_web else WhatsAppUtils.BASE_URL
        
        # Build URL
        if use_web:
            url = f"{base_url}?phone={cleaned_phone}"
        else:
            url = f"{base_url}{cleaned_phone}"
        
        # Add message if provided
        if message:
            separator = "&" if use_web else "?"
            encoded_message = urllib.parse.quote(message)
            url += f"{separator}text={encoded_message}"
        
        return url
    
    @staticmethod
    def generate_group_invite_link(invite_code: str) -> str:
        """
        Generate WhatsApp group invite link.
        
        Args:
            invite_code: Group invite code
            
        Returns:
            Complete group invite URL
            
        Example:
            >>> WhatsAppUtils.generate_group_invite_link("ABC123XYZ")
            'https://chat.whatsapp.com/ABC123XYZ'
        """
        return f"https://chat.whatsapp.com/{invite_code}"
    
    @staticmethod
    def create_template_message(
        template: str,
        variables: Dict[str, str]
    ) -> str:
        """
        Create message from template with variables.
        
        Args:
            template: Message template with {variable} placeholders
            variables: Dictionary of variable values
            
        Returns:
            Formatted message
            
        Example:
            >>> template = "Hello {name}, your order #{order_id} is ready!"
            >>> variables = {"name": "John", "order_id": "12345"}
            >>> WhatsAppUtils.create_template_message(template, variables)
            'Hello John, your order #12345 is ready!'
        """
        return template.format_map(SafeDict(variables))
    
    @staticmethod
    def generate_bulk_links(
        contacts: List[Dict[str, str]],
        message_template: str,
        use_web: bool = False
    ) -> List[Dict[str, str]]:
        """
        Generate multiple WhatsApp links for bulk messaging.
        
        Args:
            contacts: List of contact dicts with 'phone' and other fields
            message_template: Message template with {field} placeholders
            use_web: Use WhatsApp Web links
            
        Returns:
            List of contacts with added 'whatsapp_link' field
            
        Example:
            >>> contacts = [
            ...     {"phone": "+212612345678", "name": "Alice"},
            ...     {"phone": "+212698765432", "name": "Bob"}
            ... ]
            >>> template = "Hi {name}, this is a test message"
            >>> WhatsAppUtils.generate_bulk_links(contacts, template)
            [
                {
                    'phone': '+212612345678',
                    'name': 'Alice',
                    'whatsapp_link': 'https://wa.me/212612345678?text=Hi%20Alice...'
                },
                ...
            ]
        """
        results = []
        
        for contact in contacts:
            # Create personalized message
            message = WhatsAppUtils.create_template_message(
                message_template,
                contact
            )
            
            # Generate link
            link = WhatsAppUtils.generate_chat_link(
                contact['phone'],
                message,
                use_web
            )
            
            # Add link to contact info
            contact_with_link = contact.copy()
            contact_with_link['whatsapp_link'] = link
            results.append(contact_with_link)
        
        return results


class WhatsAppMessageTemplates:
    """Pre-built message templates for common use cases."""
    
    CUSTOMER_SERVICE = {
        'welcome':
            "Bonjour {name} 👋\n"
            "Bienvenue chez {business_name} ! Nous sommes ravis de vous accueillir. "
            "Comment pouvons-nous vous aider aujourd'hui ?",

        'order_confirmation':
            "Bonjour {name},\n"
            "Votre commande n°{order_id} a bien été confirmée ✅.\n"
            "Date de livraison estimée : {delivery_date}.\n"
            "Suivez votre commande ici : {tracking_url}",

        'payment_reminder':
            "Bonjour {name},\n"
            "Nous vous rappelons qu'un paiement de {amount} concernant la facture n°{invoice_id} est toujours en attente.\n"
            "N'hésitez pas à nous contacter si vous avez besoin d'assistance.",

        'appointment_reminder':
            "Bonjour {name},\n"
            "Nous vous rappelons votre rendez-vous prévu le {date} à {time}.\n"
            "Répondez « CONFIRMER » pour confirmer votre présence ou « REPORTER » pour modifier le rendez-vous.",
    }


    # Templates Marketing
    MARKETING = {
        'promotion':
            "🎉 Offre spéciale pour vous, {name} !\n"
            "Profitez de {discount}% de réduction sur {product}.\n"
            "Utilisez le code : {promo_code}\n"
            "Offre valable jusqu'au {expiry_date}.",

        'new_product':
            "Bonjour {name} 🚀\n"
            "Découvrez notre nouveau produit : {product_name}.\n"
            "Nous sommes convaincus qu'il vous plaira !\n"
            "{product_url}",

        'abandoned_cart':
            "Bonjour {name},\n"
            "Vous avez laissé {items_count} article(s) dans votre panier 🛒.\n"
            "Finalisez votre achat maintenant et bénéficiez de {discount}% de réduction !\n"
            "{cart_url}",
    }


    # Templates Éducation
    EDUCATION = {
        'class_reminder':
            "Bonjour {student_name},\n"
            "Petit rappel : votre cours de {subject} est prévu le {date} à {time} dans la salle {room}.",

        'assignment_due':
            "Bonjour {student_name},\n"
            "Votre devoir « {assignment_name} » doit être remis avant le {due_date}.\n"
            "N'oubliez pas de le soumettre à temps !",

        'grade_notification':
            "Bonjour {student_name},\n"
            "Votre note pour la matière {subject} a été publiée 📚.\n"
            "Consultez votre espace étudiant pour voir les détails.",
    }


    # Templates Santé
    HEALTHCARE = {
        'appointment_confirmation':
            "Bonjour {patient_name},\n"
            "Votre rendez-vous avec le Dr {doctor_name} est confirmé pour le {date} à {time}.\n"
            "Adresse : {clinic_address}",

        'prescription_ready':
            "Bonjour {patient_name},\n"
            "Votre ordonnance est prête et peut être récupérée à {pharmacy_name}.\n"
            "Merci de vous munir d'une pièce d'identité.",

        'test_results':
            "Bonjour {patient_name},\n"
            "Les résultats de vos analyses sont disponibles.\n"
            "Veuillez nous contacter au {phone} afin de planifier une consultation avec le Dr {doctor_name}.",
    }
    
    @classmethod
    def get_template(cls, category: str, template_name: str) -> str:
        """
        Get a specific message template.
        
        Args:
            category: Template category (e.g., 'CUSTOMER_SERVICE')
            template_name: Template name (e.g., 'welcome')
            
        Returns:
            Message template string
        """
        category_templates = getattr(cls, category.upper(), {})
        return category_templates.get(template_name, "")


# Django Integration Example
class DjangoWhatsAppMixin:
    """
    Mixin for Django models to add WhatsApp functionality.
    Add this to your Django model to enable WhatsApp links.
    """
    
    def get_whatsapp_link(self, message: Optional[str] = None) -> str:
        """
        Generate WhatsApp link for this model instance.
        Assumes model has a 'phone' field.
        """
        if not hasattr(self, 'phone'):
            raise AttributeError("Model must have a 'phone' field")
        
        return WhatsAppUtils.generate_chat_link(self.phone, message)
    
    def send_whatsapp_message(self, template_name: str, **kwargs):
        """
        Generate a WhatsApp link with a template message.
        """
        # Get model fields for template variables
        context = {
            field.name: getattr(self, field.name)
            for field in self._meta.fields
        }
        context.update(kwargs)
        
        # Create message from template
        message = template_name.format(**context)
        
        return self.get_whatsapp_link(message)


# Django View Helper Functions
def generate_whatsapp_button_html(
    phone: str,
    message: Optional[str] = None,
    button_text: str = "Chat on WhatsApp",
    css_class: str = "btn btn-success"
) -> str:
    """
    Generate HTML for a WhatsApp button.
    
    Args:
        phone: Phone number
        message: Pre-filled message
        button_text: Button label
        css_class: CSS classes for button
        
    Returns:
        HTML string for button
    """
    link = WhatsAppUtils.generate_chat_link(phone, message)
    return f'''
    <a href="{link}" 
       target="_blank" 
       rel="noopener noreferrer"
       class="{css_class}">
        <i class="bi bi-whatsapp"></i> {button_text}
    </a>
    '''


