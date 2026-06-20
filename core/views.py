from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponse, HttpResponseBadRequest
from django.views.decorators.http import require_GET
from django.utils import timezone
from datetime import datetime
from django.db.models import Q, Count, Sum
from decimal import Decimal
from datetime import timedelta, date

from .models import Student, Payment, Enrollment, Room, Teacher
from .utils import WhatsAppMessageTemplates, WhatsAppUtils, _build_room_schedule, _build_teacher_schedule, _calculate_week_stats, get_dashboard_stats, generate_receipt_pdf, calculate_student_monthly_total, generate_sessions_from_coursegroups, _annotate_conflicts
from .forms import SessionForm, StudentForm, EnrollmentForm
from django.core.paginator import Paginator
from .models import CourseGroup, Session, Attendance, SessionException
from django.views.decorators.http import require_http_methods
from django.db import transaction
from decimal import Decimal as D
from collections import defaultdict
from .filters import StudentFilter, CourseGroupFilter, TeacherFilter, RoomFilter, SessionFilter
from django.contrib import messages
from django.urls import reverse
from django.views.decorators.http import require_POST


def payment_create(request):
    """
    Enhanced cashier view with WhatsApp confirmation option
    """
    if request.method == 'GET':
        from dateutil.relativedelta import relativedelta
        # Generate last 3 months, current month, and next 2 months
        today = timezone.now().date()
        months_choices = []
        for i in range(-3, 3):
            m = today + relativedelta(months=i)
            first_day = m.replace(day=1)
            months_choices.append({
                'value': first_day.strftime('%Y-%m-%d'),
                'label': first_day.strftime('%B %Y')
            })
        
        # Let's format labels in French
        from .utils import month_name_fr
        for m in months_choices:
            dt_obj = datetime.strptime(m['value'], '%Y-%m-%d').date()
            m['label'] = f"{month_name_fr(dt_obj.month)} {dt_obj.year}"
            
        current_month_str = today.replace(day=1).strftime('%Y-%m-%d')
        
        return render(request, 'core/payment_create.html', {
            'default_student_id': request.GET.get('student_id'),
            'months_choices': months_choices,
            'current_month_str': current_month_str,
        })

    # POST -> create payment

    if request.method == 'POST':
        student_id = request.POST.get('student_id')
        amount = request.POST.get('amount')
        payment_method = request.POST.get('payment_method', 'CASH')
        month_covered = request.POST.get('month_covered')
        send_whatsapp = request.POST.get('send_whatsapp') == 'on'

        if not student_id or not amount:
            return HttpResponseBadRequest('Missing student or amount')

        student = get_object_or_404(Student, pk=student_id)

        try:
            amount_dec = Decimal(amount)
        except Exception:
            return HttpResponseBadRequest('Montant invalide')

        # default month_covered to first day of current month
        if not month_covered:
            now = timezone.now().date()
            month_covered = now.replace(day=1)
        else:
            try:
                month_covered = datetime.strptime(month_covered, '%Y-%m-%d').date()
            except Exception:
                month_covered = timezone.now().date().replace(day=1)

        payment = Payment.objects.create(
            student=student,
            amount=amount_dec,
            payment_date=timezone.now().date(),
            month_covered=month_covered,
            status='PAID',
            payment_method=payment_method,
            created_by=request.user.get_username() if hasattr(request, 'user') and request.user.is_authenticated else ''
        )

        # Update next_payment_date for active enrollments
        from .utils import get_next_month
        next_pay_date = get_next_month(payment.month_covered)
        for enrollment in student.enrollment_set.filter(is_active=True):
            enrollment.next_payment_date = next_pay_date
            enrollment.save()

        # Generate receipt PDF
        pdf_buffer = generate_receipt_pdf(payment)
        
        # If WhatsApp confirmation requested, redirect to WhatsApp confirmation page
        if send_whatsapp and student.parent_contact:
            # Save receipt temporarily (or provide download link)
            response = HttpResponse(pdf_buffer.read(), content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="receipt_{payment.receipt_number}.pdf"'
            
            # Store payment ID in session for WhatsApp confirmation redirect
            request.session['last_payment_id'] = payment.id
            
            messages.success(request, 'Paiement enregistré avec succès!')
            return redirect('core:whatsapp_payment_confirmation', payment_id=payment.id)

        response = HttpResponse(pdf_buffer.read(), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="receipt_{payment.receipt_number}.pdf"'
        return response


def receipt_download(request, payment_id):
    """
    Download PDF receipt for a given payment ID
    """
    payment = get_object_or_404(Payment, pk=payment_id)
    pdf_buffer = generate_receipt_pdf(payment)
    response = HttpResponse(pdf_buffer.read(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="receipt_{payment.receipt_number}.pdf"'
    return response




@require_GET
def student_search(request):
    """AJAX endpoint for Select2 student search. Query param `q`."""
    q = request.GET.get('q', '').strip()
    results = []
    if q:
        students = Student.objects.filter(name__icontains=q)[:20]
    else:
        students = Student.objects.all()[:20]

    for s in students:
        results.append({
            'id': s.id,
            'text': f"{s.name} ({s.parent_name or s.parent_contact})"
        })

    return JsonResponse({'results': results})


@require_GET
def student_unpaid_search(request):
    """AJAX endpoint for Select2 student search filtered to unpaid students. Query param `q`."""
    from django.utils import timezone
    
    q = request.GET.get('q', '').strip()
    
    # Get current month
    current_month = timezone.now().date().replace(day=1)
    
    # Get all students or filter by name
    if q:
        students = Student.objects.filter(name__icontains=q, is_active=True)[:50]
    else:
        students = Student.objects.filter(is_active=True)[:50]
    
    # Filter to unpaid students only
    unpaid_students = []
    for s in students:
        required = calculate_student_monthly_total(s)
        paid = Payment.objects.filter(
            student=s,
            month_covered=current_month,
            status='PAID'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
        
        if paid < required:  # Student has unpaid amount
            unpaid_students.append({
                'id': s.id,
                'text': f"{s.name} ({s.parent_name or s.parent_contact}) - Due: {required - paid} DH",
                'due_amount': str(required - paid)
            })
    
    return JsonResponse({'results': unpaid_students})


@require_GET
def student_detail(request):
    """Return student details including calculated amount due and enrollments."""
    student_id = request.GET.get('id')
    if not student_id:
        return HttpResponseBadRequest('Missing id')

    student = get_object_or_404(Student, pk=student_id)

    # Get month parameter from request, default to current month
    month_param = request.GET.get('month')
    if month_param:
        try:
            current_month = datetime.strptime(month_param, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            current_month = timezone.now().date().replace(day=1)
    else:
        current_month = timezone.now().date().replace(day=1)
    
    from .utils import calculate_student_expected_fees_for_month, count_scheduled_sessions_in_month, count_remaining_sessions_in_month
    
    required = calculate_student_expected_fees_for_month(student, current_month)
    enrollments = student.enrollment_set.filter(is_active=True).select_related('course_group')
    groups = []
    
    paid = Payment.objects.filter(
        student=student,
        month_covered=current_month,
        status='PAID'
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
    
    required = max(required - paid, Decimal('0'))
    
    for e in enrollments:
        is_prorated = False
        total_sess = 0
        rem_sess = 0
        sess_price = Decimal('0.00')
        prorated_price = e.course_group.monthly_price
        
        if e.enrolled_date.year == current_month.year and e.enrolled_date.month == current_month.month:
            if e.enrolled_date.day > 1:
                is_prorated = True
                total_sess = count_scheduled_sessions_in_month(e.course_group, current_month.year, current_month.month)
                rem_sess = count_remaining_sessions_in_month(e.course_group, e.enrolled_date)
                if total_sess > 0:
                    sess_price = (e.course_group.monthly_price / Decimal(total_sess)).quantize(Decimal('0.01'))
                    prorated_price = (Decimal(rem_sess) * sess_price).quantize(Decimal('0.01'))
        
        groups.append({
            'name': e.course_group.name,
            'price': str(e.course_group.monthly_price),
            'is_prorated': is_prorated,
            'total_sessions': total_sess,
            'remaining_sessions': rem_sess,
            'session_price': str(sess_price),
            'prorated_price': str(prorated_price)
        })

    data = {
        'id': student.id,
        'name': student.name,
        'parent_contact': student.parent_contact,
        'required': str(required),
        'groups': groups
    }

    return JsonResponse(data)


def cockpit(request):
    """Operational dashboard (cockpit) for director"""
    stats = get_dashboard_stats()

    # Red list: students unpaid (use unpaid_students from utils)
    red_list = stats.get('alerts', {}).get('unpaid_students', [])

    context = {
        'stats': stats,
        'red_list': red_list,
    }

    return render(request, 'core/dashboard.html', context)


def students_list(request):
    """List all students with filtering and pagination"""
    
    # Base queryset with optimizations
    students_qs = Student.objects.filter(
        is_active=True
    ).prefetch_related(
        'enrollment_set__course_group',
        'payments'
    ).select_related()
    
    # Apply filters
    student_filter = StudentFilter(request.GET, queryset=students_qs)
    filtered_qs = student_filter.qs.order_by('name')
    
    # Pagination
    page = request.GET.get('page', 1)
    per_page = request.GET.get('per_page', '25')
    
    try:
        per_page = int(per_page)
        if per_page not in [10, 25, 50, 100]:
            per_page = 25
    except (ValueError, TypeError):
        per_page = 25
    
    paginator = Paginator(filtered_qs, per_page)
    students = paginator.get_page(page)
    
    # Build querystring for pagination (exclude 'page' parameter)
    qs_dict = request.GET.copy()
    qs_dict.pop('page', None)
    querystring = qs_dict.urlencode()
    
    # Check if any filters are active
    filters_active = any([
        request.GET.get('q'),
        request.GET.get('payment_status'),
        request.GET.get('course_group'),
        request.GET.get('is_active') and request.GET.get('is_active') != '',
    ])
    
    context = {
        'students': students,
        'filter': student_filter,
        'per_page': per_page,
        'querystring': querystring,
        'filters_active': filters_active,
        'total_students': students_qs.count(),
        'filtered_count': filtered_qs.count(),
    }
    
    return render(request, 'core/students_list.html', context)



def student_page(request, student_id):
    """Student detail page with profile, enrollments, payments, attendance, and stats"""
    from django.db.models import Count, Q
    from .utils import get_student_payment_status
    
    student = get_object_or_404(Student, pk=student_id)

    # Enrollments
    enrollments = student.enrollment_set.filter(is_active=True).select_related('course_group')
    total_enrolled = enrollments.count()
    
    # Payment info (current month)
    current_month = timezone.now().date().replace(day=1)
    payment_status = get_student_payment_status(student, current_month)
    
    # Payment history
    payments_qs = Payment.objects.filter(student=student).order_by('-payment_date', '-created_at')
    paginator = Paginator(payments_qs, 10)
    page_number = request.GET.get('page')
    payments = paginator.get_page(page_number)
    
    # Attendance stats (last 30 days)
    from datetime import timedelta
    from_date = timezone.now().date() - timedelta(days=30)
    attendance_qs = Attendance.objects.filter(student=student, date__gte=from_date)
    total_classes = attendance_qs.count()
    attended_classes = attendance_qs.filter(is_present=True).count()
    attendance_rate = (attended_classes / total_classes * 100) if total_classes > 0 else 0
    
    # Monthly payment history (last 6 months)
    from dateutil.relativedelta import relativedelta
    from .utils import calculate_student_expected_fees_for_month
    six_months_ago = timezone.now().date() - relativedelta(months=6)
    payment_months = []
    for i in range(6):
        month_date = timezone.now().date() - relativedelta(months=i)
        month_date = month_date.replace(day=1)
        paid = Payment.objects.filter(
            student=student,
            month_covered=month_date,
            status='PAID'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
        required = calculate_student_expected_fees_for_month(student, month_date)
        remaining = max(required - paid, Decimal('0'))
        payment_months.insert(0, {
            'month': month_date.strftime('%b %Y'),
            'paid': paid,
            'required': required,
            'remaining': remaining,
            'status': 'OK' if paid >= required else 'PARTIAL' if paid > 0 else 'UNPAID'
        })


    context = {
        'student': student,
        'enrollments': enrollments,
        'total_enrolled': total_enrolled,
        'payments': payments,
        'payment_status': payment_status,
        'attendance_rate': round(attendance_rate, 1),
        'attended_classes': attended_classes,
        'total_classes': total_classes,
        'payment_months': payment_months,
    }

    return render(request, 'core/student_detail.html', context)


def sessions_today(request):
    """Enhanced session view with navigation and statistics"""
    
    # Determine the date to display
    date_param = request.GET.get('date')
    if date_param:
        try:
            from datetime import datetime
            view_date = datetime.strptime(date_param, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            view_date = timezone.now().date()
    else:
        view_date = timezone.now().date()
    
    today = timezone.now().date()
    
    # Calculate navigation dates
    prev_day = view_date - timedelta(days=1)
    next_day = view_date + timedelta(days=1)
    
    # Base queryset for the view date
    sessions_qs = Session.objects.filter(
        date=view_date
    ).select_related(
        'group',
        'group__teacher',
        'room'
    ).prefetch_related(
        'group__students'
    ).order_by('start_time')
    
    # Apply filters
    session_filter = SessionFilter(request.GET, queryset=sessions_qs)
    sessions = session_filter.qs
    
    # Calculate statistics
    stats = {
        'total': sessions.count(),
        'planned': sessions.filter(status='PLANNED').count(),
        'done': sessions.filter(status='DONE').count(),
        'cancelled': sessions.filter(status='CANCELLED').count(),
    }
    
    # Check if any filters are active (excluding date parameter)
    filters_active = any([
        request.GET.get('date_after'),
        request.GET.get('date_before'),
        request.GET.get('room'),
        request.GET.get('teacher'),
        request.GET.get('status'),
        request.GET.get('group_name'),
    ])
    
    # Build querystring for navigation (preserve filters)
    qs_dict = request.GET.copy()
    qs_dict.pop('date', None)  # Remove date to add it dynamically
    querystring = qs_dict.urlencode()
    
    context = {
        'sessions': sessions,
        'view_date': view_date,
        'today': today,
        'prev_day': prev_day,
        'next_day': next_day,
        'is_today': view_date == today,
        'filter': session_filter,
        'stats': stats,
        'filters_active': filters_active,
        'querystring': querystring,
    }
    
    return render(request, 'core/sessions_today.html', context)

@require_http_methods(['GET', 'POST'])
def session_create(request):
    """Create a new session (class)"""
    if request.method == 'POST':
        form = SessionForm(request.POST)
        if form.is_valid():
            session = form.save(commit=False)
            try:
                session.is_manually_edited = True
                session.full_clean()
                session.save()
            except Exception as e:
                form.add_error(None, str(e))
            else:
                return render(request, 'core/session_form_saved.html', {'session': session})
    else:
        form = SessionForm()

    return render(request, 'core/session_form.html', {'form': form, 'action': 'Créer'})


@require_http_methods(['GET', 'POST'])
def session_edit(request, session_id):
    """Edit an existing session"""
    session = get_object_or_404(Session, pk=session_id)
    if request.method == 'POST':
        form = SessionForm(request.POST, instance=session)
        if form.is_valid():
            s = form.save(commit=False)
            try:
                s.is_manually_edited = True
                s.full_clean()
                s.save()
            except Exception as e:
                form.add_error(None, str(e))
            else:
                return render(request, 'core/session_form_saved.html', {'session': s})
    else:
        form = SessionForm(instance=session)

    return render(request, 'core/session_form.html', {'form': form, 'action': 'Modifier', 'session': session})


@require_http_methods(['POST'])
def session_delete(request, session_id):
    session = get_object_or_404(Session, pk=session_id)
    session.delete()
    return render(request, 'core/session_deleted.html', {'session_id': session_id})


@require_http_methods(['GET', 'POST'])
def session_attendance(request, session_id):
    """Show attendance checklist for a session and save attendance.

    Business rule: default all present; admin unchecks absentees.
    """
    session = get_object_or_404(Session, pk=session_id)
    students = session.group.students.filter(is_active=True)

    if request.method == 'GET':
        # prefill: check if Attendance exists for this date/group
        existing = Attendance.objects.filter(course_group=session.group, date=session.date)
        present_map = {a.student_id: a.is_present for a in existing}
        students_list = []
        for s in students:
            # default to True (present) when no record exists
            checked = present_map.get(s.id, True)
            students_list.append({'student': s, 'checked': checked})

        return render(request, 'core/session_attendance.html', {
            'session': session,
            'students_list': students_list,
        })

    # POST: process attendance form
    # expected: checkbox 'present_<student_id>' for those present
    with transaction.atomic():
        for student in students:
            key = f'present_{student.id}'
            is_present = key in request.POST
            att, created = Attendance.objects.update_or_create(
                student=student,
                course_group=session.group,
                date=session.date,
                defaults={'is_present': is_present}
            )

    # mark session as DONE if attendance saved
    session.status = 'DONE'
    session.save()

    return render(request, 'core/session_attendance_saved.html', {'session': session})


def teacher_payroll(request):
    """Calculate payroll for a teacher over a date range."""
    from .models import Teacher, Session
    from .utils import calculate_teacher_hours
    
    teacher_qs = Teacher.objects.filter(is_active=True)

    result = None
    if request.method == 'POST':
        teacher_id = request.POST.get('teacher_id')
        start = request.POST.get('start_date')
        end = request.POST.get('end_date')
        if not (teacher_id and start and end):
            return HttpResponseBadRequest('Missing parameters')
        teacher = get_object_or_404(Teacher, pk=teacher_id)
        start_d = datetime.strptime(start, '%Y-%m-%d').date()
        end_d = datetime.strptime(end, '%Y-%m-%d').date()

        # Get sessions list for reference
        sessions = Session.objects.filter(
            group__teacher=teacher,
            status='DONE',
            date__range=[start_d, end_d]
        ).order_by('date', 'start_time')

        sessions_list = []
        for s in sessions:
            sessions_list.append({'session': s, 'hours': s.duration_hours()})

        # Calculate earnings using our upgraded helper
        payroll_data = calculate_teacher_hours(teacher, start_d, end_d)

        result = {
            'teacher': teacher,
            'sessions': sessions_list,
            **payroll_data
        }

    return render(request, 'core/teacher_payroll.html', {'teacher_qs': teacher_qs, 'result': result})


def courses_list(request):
    """Display all course groups (classes) with summary info."""
    from .models import CourseGroup
    courses = CourseGroup.objects.all().select_related('teacher').prefetch_related('schedules__room')
    
    # Annotate with enrollment count
    from django.db.models import Count
    courses = courses.annotate(enrollment_count=Count('enrollment'))

    course_filter = CourseGroupFilter(request.GET, queryset=courses)
    courses = course_filter.qs

    return render(request, 'core/courses_list.html', {'courses': courses, 'filter': course_filter})


def group_detail(request, group_id):
    """Detailed view for a single CourseGroup."""
    from .models import CourseGroup, Session, Payment
    from django.db.models import Sum, Q
    from django.utils import timezone

    group = get_object_or_404(
        CourseGroup.objects.select_related('teacher', 'level').prefetch_related('schedules__room'),
        pk=group_id
    )

    # Students enrolled in this group
    students = group.students.select_related('level').order_by('name')
    total_students = students.count()

    # Monthly revenue estimate
    monthly_revenue = group.monthly_price * total_students

    # Sessions stats
    sessions_qs = Session.objects.filter(group=group).select_related('room', 'substitute_teacher')
    total_sessions = sessions_qs.count()
    planned_sessions = sessions_qs.filter(status='PLANNED').count()
    done_sessions = sessions_qs.filter(status='DONE').count()
    cancelled_sessions = sessions_qs.filter(status='CANCELLED').count()

    # Recent/upcoming sessions (last 10 done + next 10 planned)
    from datetime import date
    today = date.today()
    past_sessions = list(sessions_qs.filter(date__lt=today).order_by('-date')[:10])
    future_sessions = list(sessions_qs.filter(date__gte=today).order_by('date')[:10])
    recent_sessions = sorted(past_sessions + future_sessions, key=lambda s: s.date, reverse=True)

    # Current month payments for students in this group
    now = timezone.now()
    current_month_payments = Payment.objects.filter(
        student__in=students,
        payment_date__year=now.year,
        payment_date__month=now.month,
    ).select_related('student').order_by('-payment_date')
    current_month_total = current_month_payments.aggregate(t=Sum('amount'))['t'] or 0

    schedules = group.schedules.all()

    return render(request, 'core/course_group_detail.html', {
        'group': group,
        'students': students,
        'total_students': total_students,
        'monthly_revenue': monthly_revenue,
        'schedules': schedules,
        'total_sessions': total_sessions,
        'planned_sessions': planned_sessions,
        'done_sessions': done_sessions,
        'cancelled_sessions': cancelled_sessions,
        'recent_sessions': recent_sessions,
        'current_month_payments': current_month_payments,
        'current_month_total': current_month_total,
    })


def teachers_list(request):
    """Display all teachers with summary info."""
    from .models import Teacher

    teachers = Teacher.objects.annotate(
        course_count=Count('course_groups', distinct=True),
        session_count=Count(
            'course_groups__sessions',
            filter=Q(course_groups__sessions__status='PLANNED'),
            distinct=True
        )
    )

    teacher_filter = TeacherFilter(request.GET, queryset=teachers)
    teachers = teacher_filter.qs

    return render(request, 'core/teachers_list.html', {'teachers': teachers, 'filter': teacher_filter})

def rooms_list(request):
    """Display all rooms with summary info."""
    from .models import Room
    from django.db.models import Count
    
    rooms = Room.objects.all()
    rooms = rooms.annotate(
        course_count=Count('schedules__course_group', distinct=True),
        session_count=Count('sessions', filter=Q(sessions__status='PLANNED'), distinct=True)
    )
    
    room_filter = RoomFilter(request.GET, queryset=rooms)
    rooms = room_filter.qs

    return render(request, 'core/rooms_list.html', {'rooms': rooms, 'filter': room_filter})


def sessions_schedule(request):
    """Enhanced weekly schedule view with better structure and filtering"""
    
    # Get the week starting date (Monday)
    today = timezone.now().date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    
    # Get week parameter from request
    week_param = request.GET.get('week')
    if week_param:
        try:
            parsed = datetime.strptime(week_param, '%Y-%m-%d').date()
            # Normalize to Monday
            week_start = parsed - timedelta(days=parsed.weekday())
            week_end = week_start + timedelta(days=6)
        except (ValueError, TypeError):
            pass  # Keep current week
    
    # Determine view mode (room-based or teacher-based)
    view_mode = request.GET.get('view', 'room')  # 'room' or 'teacher'
    
    # Get filter parameters
    room_filter = request.GET.get('room_id')
    teacher_filter = request.GET.get('teacher_id')
    status_filter = request.GET.get('status')
    
    # Build list of dates for the week
    dates = [week_start + timedelta(days=i) for i in range(7)]
    
    # Base sessions queryset for the week
    base_sessions = Session.objects.filter(
        date__range=[week_start, week_end]
    ).select_related(
        'group',
        'group__teacher',
        'room'
    ).prefetch_related(
        'group__students'
    )
    
    # Apply filters
    if room_filter:
        base_sessions = base_sessions.filter(room_id=room_filter)
    if teacher_filter:
        base_sessions = base_sessions.filter(group__teacher_id=teacher_filter)
    if status_filter:
        base_sessions = base_sessions.filter(status=status_filter)
    
    # Get all rooms and teachers for the filters
    rooms = Room.objects.filter(is_active=True).order_by('name')
    teachers = Teacher.objects.filter(is_active=True).order_by('name')
    
    # Annotate and load sessions in-memory
    annotated_sessions = _annotate_conflicts(base_sessions)
    
    # Build schedule grid based on view mode
    if view_mode == 'teacher':
        rows = _build_teacher_schedule(teachers, dates, annotated_sessions)
        row_label = 'Professeur'
    else:
        rows = _build_room_schedule(rooms, dates, annotated_sessions)
        row_label = 'Salle'
    
    # Build date labels with weekday names
    weekdays = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche']
    date_labels = [
        {
            'weekday': weekdays[i],
            'date': date,
            'is_today': date == today,
            'is_weekend': i >= 5
        }
        for i, date in enumerate(dates)
    ]
    
    # Calculate statistics
    stats = _calculate_week_stats(annotated_sessions, dates)
    
    # Check if filters are active
    filters_active = any([room_filter, teacher_filter, status_filter])
    
    context = {
        'week_start': week_start,
        'week_end': week_end,
        'prev_week': week_start - timedelta(days=7),
        'next_week': week_start + timedelta(days=7),
        'dates': dates,
        'date_labels': date_labels,
        'rows': rows,
        'row_label': row_label,
        'view_mode': view_mode,
        'rooms': rooms,
        'teachers': teachers,
        'stats': stats,
        'today': today,
        'filters_active': filters_active,
        'room_filter': room_filter,
        'teacher_filter': teacher_filter,
        'status_filter': status_filter,
        'courses': CourseGroup.objects.filter(is_active=True).order_by('name'),
    }
    
    return render(request, 'core/sessions_schedule.html', context)

@require_POST
def session_quick_status_update(request, session_id):
    """
    Quick update session status via AJAX
    Used for marking sessions as done/cancelled from schedule view
    """
    session = get_object_or_404(Session, id=session_id)
    new_status = request.POST.get('status')
    
    if new_status not in ['PLANNED', 'DONE', 'CANCELLED']:
        return JsonResponse({'success': False, 'error': 'Invalid status'}, status=400)
    
    session.status = new_status
    session.save()
    
    return JsonResponse({
        'success': True,
        'session_id': session.id,
        'new_status': new_status,
        'message': f'Statut mis à jour: {session.get_status_display()}'
    })


def session_detail_ajax(request, session_id):
    """
    Get session details for modal display
    """
    from django.conf import settings
    session = get_object_or_404(
        Session.objects.select_related(
            'group',
            'group__teacher',
            'room',
            'substitute_teacher'
        ).prefetch_related(
            'group__students'
        ),
        id=session_id
    )
    
    # Get attendance if exists
    from .models import Attendance
    attendance = Attendance.objects.filter(
        course_group=session.group,
        date=session.date
    ).select_related('student')
    
    students = session.group.students.all()
    attendance_dict = {a.student_id: a.is_present for a in attendance}
    
    student_list = []
    for student in students:
        student_list.append({
            'id': student.id,
            'name': student.name,
            'is_present': attendance_dict.get(student.id),
        })
        
    current_time = timezone.localtime(timezone.now()) if settings.USE_TZ else datetime.now()
    
    if settings.USE_TZ:
        import zoneinfo
        local_tz = timezone.get_current_timezone()
        session_start = datetime.combine(session.date, session.start_time).replace(tzinfo=local_tz)
        session_end = datetime.combine(session.date, session.end_time).replace(tzinfo=local_tz)
    else:
        session_start = datetime.combine(session.date, session.start_time)
        session_end = datetime.combine(session.date, session.end_time)

    # editable if before session ends
    is_past = current_time >= session_end
    is_future = current_time < session_end
    
    data = {
        'id': session.id,
        'group': {
            'id': session.group.id,
            'name': session.group.name,
            'subject': session.group.subject,
            'level': session.group.level.name if session.group.level else '',
            'level_id': session.group.level.id if session.group.level else None,
        },
        'date': session.date.strftime('%Y-%m-%d'),
        'start_time': session.start_time.strftime('%H:%M'),
        'end_time': session.end_time.strftime('%H:%M'),
        'duration': session.duration_hours(),
        'room': {
            'id': session.room.id,
            'name': session.room.name,
            'capacity': session.room.capacity,
        },
        'teacher': {
            'id': session.group.teacher.id,
            'name': session.group.teacher.name,
            'phone': session.group.teacher.phone,
        },
        'substitute_teacher': {
            'id': session.substitute_teacher.id,
            'name': session.substitute_teacher.name,
        } if session.substitute_teacher else None,
        'substitute_teacher_id': session.substitute_teacher_id,
        'status': session.status,
        'status_display': session.get_status_display(),
        'students': student_list,
        'student_count': len(student_list),
        'notes': session.notes,
        'is_past': is_past,
        'is_future': is_future,
    }
    
    return JsonResponse(data)


@require_POST
def session_create_ajax(request):
    """
    AJAX POST endpoint to create a new session.
    """
    from datetime import datetime as dt
    from django.core.exceptions import ValidationError
    
    group_id = request.POST.get('group_id')
    date_str = request.POST.get('date')
    room_id = request.POST.get('room_id')
    start_time_str = request.POST.get('start_time')
    end_time_str = request.POST.get('end_time')
    substitute_teacher_id = request.POST.get('substitute_teacher_id')
    notes = request.POST.get('notes', '')
    
    if not (group_id and date_str and room_id and start_time_str and end_time_str):
        return JsonResponse({'success': False, 'error': 'Paramètres manquants.'}, status=400)
        
    try:
        group = get_object_or_404(CourseGroup, id=group_id)
        room = get_object_or_404(Room, id=room_id)
        
        date_obj = dt.strptime(date_str, '%Y-%m-%d').date()
        
        try:
            start_time = dt.strptime(start_time_str, '%H:%M:%S').time()
        except ValueError:
            start_time = dt.strptime(start_time_str, '%H:%M').time()
            
        try:
            end_time = dt.strptime(end_time_str, '%H:%M:%S').time()
        except ValueError:
            end_time = dt.strptime(end_time_str, '%H:%M').time()
        
        sub_teacher = None
        if substitute_teacher_id:
            sub_teacher = get_object_or_404(Teacher, id=substitute_teacher_id)
            
        session = Session(
            group=group,
            date=date_obj,
            start_time=start_time,
            end_time=end_time,
            room=room,
            status='PLANNED',
            notes=notes,
            is_manually_edited=True
        )
        if sub_teacher:
            session.substitute_teacher = sub_teacher
            
        session.full_clean()
        session.save()
        
    except ValidationError as ve:
        error_msg = "; ".join(ve.messages) if hasattr(ve, 'messages') else str(ve)
        return JsonResponse({'success': False, 'error': error_msg}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
        
    return JsonResponse({
        'success': True,
        'session_id': session.id,
        'message': 'Session créée avec succès.'
    })


@require_POST
def session_update_ajax(request, session_id):
    """
    AJAX POST endpoint to update session date, room, times, notes, or substitute teacher.
    """
    from datetime import datetime as dt
    from django.core.exceptions import ValidationError
    
    session = get_object_or_404(Session, id=session_id)
    
    date_str = request.POST.get('date')
    room_id = request.POST.get('room_id')
    teacher_id = request.POST.get('teacher_id')
    start_time_str = request.POST.get('start_time')
    end_time_str = request.POST.get('end_time')
    notes = request.POST.get('notes')
    
    if date_str:
        try:
            session.date = dt.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return JsonResponse({'success': False, 'error': 'Format de date invalide.'}, status=400)
            
    if room_id:
        session.room_id = int(room_id)
        
    if teacher_id is not None:
        if teacher_id == "":
            session.substitute_teacher = None
        else:
            try:
                teacher_id_int = int(teacher_id)
                if session.group.teacher_id == teacher_id_int:
                    session.substitute_teacher = None
                else:
                    session.substitute_teacher_id = teacher_id_int
            except ValueError:
                pass
            
    if start_time_str:
        try:
            session.start_time = dt.strptime(start_time_str, '%H:%M').time()
        except ValueError:
            try:
                session.start_time = dt.strptime(start_time_str, '%H:%M:%S').time()
            except ValueError:
                return JsonResponse({'success': False, 'error': 'Format de début de session invalide.'}, status=400)
                
    if end_time_str:
        try:
            session.end_time = dt.strptime(end_time_str, '%H:%M').time()
        except ValueError:
            try:
                session.end_time = dt.strptime(end_time_str, '%H:%M:%S').time()
            except ValueError:
                return JsonResponse({'success': False, 'error': 'Format de fin de session invalide.'}, status=400)
                
    if notes is not None:
        session.notes = notes
        
    try:
        session.is_manually_edited = True
        session.full_clean()
        session.save()
    except ValidationError as ve:
        error_msg = "; ".join(ve.messages) if hasattr(ve, 'messages') else str(ve)
        return JsonResponse({'success': False, 'error': error_msg}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
        
    return JsonResponse({
        'success': True,
        'session_id': session.id,
        'date': session.date.strftime('%Y-%m-%d'),
        'start_time': session.start_time.strftime('%H:%M'),
        'end_time': session.end_time.strftime('%H:%M'),
        'room_id': session.room_id,
        'room_name': session.room.name,
        'substitute_teacher_id': session.substitute_teacher_id,
        'message': 'Session mise à jour avec succès.'
    })


@require_http_methods(['GET', 'POST'])
def session_generate_bulk(request):
    """On-demand generate/update sessions for a date range."""
    from datetime import timedelta
    
    summary = None
    errors = []
    
    if request.method == 'POST':
        weeks = int(request.POST.get('weeks', 4))
        force = request.POST.get('force') == 'on'
        
        today = timezone.now().date()
        start_date = today
        end_date = today + timedelta(weeks=weeks)
        
        try:
            summary = generate_sessions_from_coursegroups(start_date, end_date, force=force)
        except Exception as e:
            errors.append(str(e))
    
    return render(request, 'core/session_generate.html', {
        'summary': summary,
        'errors': errors,
    })


@require_http_methods(['GET', 'POST'])
def session_exceptions_list(request):
    """List and manage session exceptions."""
    from .models import Room
    
    exceptions = SessionException.objects.select_related('course_group', 'override_room').prefetch_related('course_group__schedules__room').order_by('-date')
    
    # Filter by course_group if provided
    group_id = request.GET.get('group_id')
    if group_id:
        exceptions = exceptions.filter(course_group_id=group_id)
    
    # Form submission: create/edit exception
    if request.method == 'POST':
        action = request.POST.get('action')
        group_id = int(request.POST.get('group_id'))
        date_str = request.POST.get('date')
        from datetime import datetime as dt
        try:
            date_obj = dt.strptime(date_str, '%Y-%m-%d').date()
        except Exception:
            return HttpResponseBadRequest('Invalid date')
        
        group = get_object_or_404(CourseGroup, pk=group_id)
        
        if action == 'override':
            override_room_id = request.POST.get('override_room_id')
            override_start = request.POST.get('override_start_time')
            override_end = request.POST.get('override_end_time')
            new_date_str = request.POST.get('new_date') or None
            from datetime import time as dttime
            # Parse new date if provided
            if new_date_str:
                try:
                    new_date = dt.strptime(new_date_str, '%Y-%m-%d').date()
                except Exception:
                    return HttpResponseBadRequest('Invalid new date')
            else:
                new_date = date_obj

            # Parse times
            try:
                override_start_time = dttime.fromisoformat(override_start) if override_start else None
                to_time = dttime.fromisoformat(override_end) if override_end else None
            except Exception:
                return HttpResponseBadRequest('Invalid time format, use HH:MM')

            # If user moved the exception to a new date, create/update for the new date and remove the old one
            from django.core.exceptions import ValidationError
            try:
                # Create/update the exception at target date
                exc, created = SessionException.objects.update_or_create(
                    course_group=group,
                    date=new_date,
                    defaults={
                        'cancelled': False,
                        'override_room_id': override_room_id or None,
                        'override_start_time': override_start_time,
                        'override_end_time': to_time,
                    }
                )
                # If original date differs, delete the old exception
                if new_date != date_obj:
                    SessionException.objects.filter(course_group=group, date=date_obj).exclude(pk=exc.pk).delete()
            except ValidationError as ve:
                return HttpResponseBadRequest('; '.join(ve.messages))
            except Exception as e:
                return HttpResponseBadRequest(str(e))
        elif action == 'delete':
            SessionException.objects.filter(course_group=group, date=date_obj).delete()

        # Regenerate sessions affected by this exception.
        # If the exception was moved to a different date, regenerate around both dates.
        from datetime import timedelta
        affected_dates = [date_obj]
        try:
            if action == 'override' and 'new_date' in locals() and new_date and new_date != date_obj:
                affected_dates.append(new_date)
        except Exception:
            pass

        start = min(affected_dates) - timedelta(days=1)
        end = max(affected_dates) + timedelta(days=1)
        generate_sessions_from_coursegroups(start, end, force=True)
        
        return render(request, 'core/session_exceptions_saved.html', {'group': group, 'date': date_obj})
    
    courses = CourseGroup.objects.filter(is_active=True).prefetch_related('schedules__room').order_by('name')
    rooms = Room.objects.all()
    
    return render(request, 'core/session_exceptions_list.html', {
        'exceptions': exceptions,
        'courses': courses,
        'rooms': rooms,
        'selected_group_id': group_id,
    })


# =====================
# STUDENT CRUD VIEWS
# =====================

def student_create(request):
    """Create a new student"""
    if request.method == 'POST':
        form = StudentForm(request.POST)
        if form.is_valid():
            student = form.save()
            messages.success(request, f'Élève {student.name} créé avec succès!')
            return redirect('core:student_page', student_id=student.id)
    else:
        form = StudentForm()
    
    return render(request, 'core/student_form.html', {
        'form': form,
        'title': 'Ajouter un nouvel élève',
        'button_text': 'Créer élève'
    })


def student_edit(request, student_id):
    """Edit an existing student"""
    student = get_object_or_404(Student, pk=student_id)
    
    if request.method == 'POST':
        form = StudentForm(request.POST, instance=student)
        if form.is_valid():
            form.save()
            messages.success(request, f'Élève {student.name} mise à jour avec succès!')
            return redirect('core:student_page', student_id=student.id)
    else:
        form = StudentForm(instance=student)
    
    return render(request, 'core/student_form.html', {
        'form': form,
        'student': student,
        'title': f'Modifier - {student.name}',
        'button_text': 'Mettre à jour'
    })


@require_POST
def student_delete(request, student_id):
    """Delete a student"""
    student = get_object_or_404(Student, pk=student_id)
    student_name = student.name
    student.delete()
    messages.success(request, f'Élève {student_name} supprimé avec succès!')
    return redirect('core:students_list')


def student_delete_confirm(request, student_id):
    """Confirmation page before deleting a student"""
    student = get_object_or_404(Student, pk=student_id)
    
    # Get student's enrollments and related payments
    enrollments = student.enrollment_set.all()
    payments = student.payments.all()
    
    return render(request, 'core/student_delete_confirm.html', {
        'student': student,
        'enrollments': enrollments,
        'payment_count': payments.count(),
    })


@require_POST
def enrollment_add(request, student_id):
    """Add an enrollment for a student"""
    student = get_object_or_404(Student, pk=student_id)
    course_group_id = request.POST.get('course_group_id')
    
    if not course_group_id:
        messages.error(request, 'Veuillez sélectionner un groupe de cours')
        return redirect('core:student_page', student_id=student_id)
    
    course_group = get_object_or_404(CourseGroup, pk=course_group_id)
    
    # Check if already enrolled
    if student.enrollment_set.filter(course_group=course_group).exists():
        messages.warning(request, f'{student.name} est déjà inscrit à {course_group.name}')
        return redirect('core:student_page', student_id=student_id)
    
    enrollment = Enrollment.objects.create(
        student=student,
        course_group=course_group,
        is_active=True
    )
    
    messages.success(request, f'Inscription à {course_group.name} ajoutée!')
    return redirect('core:student_page', student_id=student_id)


@require_POST
def enrollment_remove(request, enrollment_id):
    """Remove an enrollment (AJAX endpoint)"""
    enrollment = get_object_or_404(Enrollment, id=enrollment_id)
    student = enrollment.student
    course_name = enrollment.course_group.name
    
    # Store info before deletion
    student_id = student.id
    
    # Delete the enrollment
    enrollment.delete()
    
    # Return JSON response for AJAX
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({
            'success': True,
            'message': f'L\'inscription au groupe "{course_name}" a été retirée avec succès.',
            'student_id': student_id,
            'new_total': float(student.total_monthly_fees())
        })
    
    # Fallback for non-AJAX requests
    messages.success(request, f'L\'inscription au groupe "{course_name}" a été retirée avec succès.')
    return redirect('core:student_page', student_id=student_id)

###############################  WHATSAPP INTEGRATION  #######################################

@require_GET
def whatsapp_payment_reminders(request):
    """Generate WhatsApp links for payment reminders to unpaid students"""
    from django.utils import timezone
    
    current_month = timezone.now().date().replace(day=1)
    
    # Get all active students
    students = Student.objects.filter(is_active=True)
    
    # Build list of unpaid students with WhatsApp links
    unpaid_contacts = []
    
    for student in students:
        required = calculate_student_monthly_total(student)
        paid = Payment.objects.filter(
            student=student,
            month_covered=current_month,
            status='PAID'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
        
        due_amount = required - paid
        
        if due_amount > 0 and student.parent_contact:
            # Prepare contact data
            contact = {
                'phone': student.parent_contact,
                'name': student.parent_name or 'Parent',
                'student_name': student.name,
                'amount': str(due_amount),
                'currency': 'DH',
                'month': current_month.strftime('%B %Y'),
            }
            
            # Generate personalized message
            template = WhatsAppMessageTemplates.CUSTOMER_SERVICE['payment_reminder']
            message = WhatsAppUtils.create_template_message(
                template,
                {
                    'name': contact['name'],
                    'amount': f"{contact['amount']} {contact['currency']}",
                    'invoice_id': f"{student.id}-{current_month.strftime('%Y%m')}"
                }
            )
            
            # Generate WhatsApp link
            whatsapp_link = WhatsAppUtils.generate_chat_link(
                contact['phone'],
                message
            )
            
            contact['whatsapp_link'] = whatsapp_link
            contact['message'] = message
            contact['student'] = student
            contact['due_amount'] = due_amount
            
            unpaid_contacts.append(contact)
    
    context = {
        'unpaid_contacts': unpaid_contacts,
        'total_unpaid': len(unpaid_contacts),
        'current_month': current_month,
    }
    
    return render(request, 'core/whatsapp_payment_reminders.html', context)


@require_GET
def whatsapp_absence_notifications(request):
    """Generate WhatsApp links to notify parents of student absences"""

    # Day-of-week mapping (Python weekday() -> CourseGroupSchedule.day code)
    WEEKDAY_TO_CODE = {0: 'MON', 1: 'TUE', 2: 'WED', 3: 'THU', 4: 'FRI', 5: 'SAT', 6: 'SUN'}

    # Get date parameter (default to today)
    date_param = request.GET.get('date')
    if date_param:
        try:
            target_date = datetime.strptime(date_param, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            target_date = timezone.now().date()
    else:
        target_date = timezone.now().date()

    day_code = WEEKDAY_TO_CODE[target_date.weekday()]

    # Get all absence records for the date, prefetching schedules to avoid N+1
    absences = Attendance.objects.filter(
        date=target_date,
        is_present=False
    ).select_related(
        'student',
        'course_group',
        'course_group__teacher',
    ).prefetch_related(
        'course_group__schedules__room'
    )

    # Build notification contacts
    absence_contacts = []

    for absence in absences:
        student = absence.student
        course = absence.course_group

        if not student.parent_contact:
            continue

        # Find the schedule slot that matches the absence date's weekday
        matching_schedule = next(
            (s for s in course.schedules.all() if s.day == day_code),
            None
        )

        if matching_schedule:
            time_str = f"{matching_schedule.start_time.strftime('%H:%M')} - {matching_schedule.end_time.strftime('%H:%M')}"
            room_str = matching_schedule.room.name if matching_schedule.room else ''
        else:
            # Fallback: show all schedules for this course
            slots = course.schedules.all()
            if slots:
                s = slots[0]
                time_str = f"{s.start_time.strftime('%H:%M')} - {s.end_time.strftime('%H:%M')}"
                room_str = s.room.name if s.room else ''
            else:
                time_str = ''
                room_str = ''

        contact = {
            'phone': student.parent_contact,
            'name': student.parent_name or 'Parent',
            'student_name': student.name,
            'course_name': course.name,
            'date': target_date.strftime('%d/%m/%Y'),
            'time': time_str,
            'room': room_str,
            'teacher': course.teacher.name if course.teacher else '',
            'schedule': matching_schedule,
        }

        # Generate personalised absence message

        message = (
            f"Bonjour {contact['name']} 👋,\n\n"
            f"📢 Nous vous informons que {contact['student_name']} "
            f"n'a pas assisté au cours de {contact['course_name']}"
        )

        if contact['time']:
            message += f" 🕒 prévu à {contact['time']}"

        message += (
            f" le 📅 {contact['date']}.\n\n"
            "ℹ️ Si cette absence est due à une raison particulière ou si vous souhaitez obtenir "
            "plus d'informations, n'hésitez pas à nous contacter.\n\n"
            "🤝 Merci de votre confiance.\n\n"
            "Cordialement,\n"
            "🎓 L'équipe pédagogique"
        )

        # Generate WhatsApp link
        whatsapp_link = WhatsAppUtils.generate_chat_link(contact['phone'], message)

        contact['whatsapp_link'] = whatsapp_link
        contact['message'] = message
        contact['student'] = student
        contact['absence'] = absence

        absence_contacts.append(contact)

    context = {
        'absence_contacts': absence_contacts,
        'total_absences': len(absence_contacts),
        'target_date': target_date,
    }

    return render(request, 'core/whatsapp_absence_notifications.html', context)



@require_GET
def whatsapp_bulk_announcements(request):
    """Create bulk WhatsApp announcement links for all active students"""
    
    students = Student.objects.filter(is_active=True)
    
    # Build contacts list
    contacts = []
    for student in students:
        if student.parent_contact:
            contacts.append({
                'phone': student.parent_contact,
                'name': student.parent_name or 'Parent',
                'student_name': student.name,
            })
    
    # If POST, generate links with custom message
    if request.method == 'POST':
        message_template = request.POST.get('message_template', '')
        
        if message_template:
            # Generate bulk links
            bulk_links = WhatsAppUtils.generate_bulk_links(
                contacts,
                message_template
            )
            
            context = {
                'bulk_links': bulk_links,
                'message_template': message_template,
                'total_contacts': len(bulk_links),
            }
            
            return render(request, 'core/whatsapp_bulk_results.html', context)
    
    # GET request - show form
    context = {
        'contacts': contacts,
        'total_contacts': len(contacts),
        'templates': {
            'general': "Bonjour {name}, message général pour tous les parents...",
            'event': "Bonjour {name}, nous organisons un événement le [DATE]. Votre enfant {student_name} est invité à participer.",
            'closure': "Bonjour {name}, l'établissement sera fermé du [DATE] au [DATE]. Les cours reprendront le [DATE].",
        }
    }
    
    return render(request, 'core/whatsapp_bulk_announcements.html', context)


@require_GET
def whatsapp_payment_confirmation(request, payment_id):
    """Generate WhatsApp link to send payment confirmation"""
    
    payment = get_object_or_404(Payment, pk=payment_id)
    student = payment.student
    
    if not student.parent_contact:
        messages.error(request, "Aucun numéro de téléphone disponible pour ce parent")
        return redirect('core:student_page', student_id=student.id)
    
    # Generate confirmation message
    message = f"Bonjour {student.parent_name or 'Parent'},\n\n"
    message += f"Nous confirmons la réception de votre paiement:\n\n"
    message += f"Montant: {payment.amount} DH\n"
    message += f"Date: {payment.payment_date.strftime('%d/%m/%Y')}\n"
    message += f"Reçu N°: {payment.receipt_number}\n"
    message += f"Pour le mois de: {payment.month_covered.strftime('%B %Y')}\n\n"
    message += "Merci pour votre confiance!\n\n"
    message += "Cordialement,\nL'équipe administrative"
    
    # Generate WhatsApp link
    whatsapp_link = WhatsAppUtils.generate_chat_link(
        student.parent_contact,
        message
    )
    
    context = {
        'payment': payment,
        'student': student,
        'whatsapp_link': whatsapp_link,
        'message': message,
    }
    
    return render(request, 'core/whatsapp_payment_confirmation.html', context)


@require_GET
def whatsapp_session_reminder(request, session_id):
    """Generate WhatsApp links to remind students about upcoming session"""
    
    session = get_object_or_404(
        Session.objects.select_related('group', 'group__teacher', 'schedule__room'),
        pk=session_id
    )
    
    students = session.group.students.filter(is_active=True)
    
    # Build reminder contacts
    reminder_contacts = []
    
    for student in students:
        if student.parent_contact:
            contact = {
                'phone': student.parent_contact,
                'name': student.parent_name or 'Parent',
                'student_name': student.name,
                'course_name': session.group.name,
                'date': session.date.strftime('%d/%m/%Y'),
                'time': session.start_time.strftime('%H:%M'),
                'room': (session.schedule.room.name if session.schedule and session.schedule.room else 'N/A'),
            }
            
            # Use template
            template = WhatsAppMessageTemplates.EDUCATION['class_reminder']
            message = WhatsAppUtils.create_template_message(
                template,
                {
                    'student_name': contact['student_name'],
                    'subject': contact['course_name'],
                    'date': f"{contact['date']} à {contact['time']}",
                    'room': contact['room'],
                }
            )
            
            whatsapp_link = WhatsAppUtils.generate_chat_link(
                contact['phone'],
                message
            )
            
            contact['whatsapp_link'] = whatsapp_link
            contact['message'] = message
            contact['student'] = student
            
            reminder_contacts.append(contact)
    
    context = {
        'session': session,
        'reminder_contacts': reminder_contacts,
        'total_students': len(reminder_contacts),
    }
    
    return render(request, 'core/whatsapp_session_reminder.html', context)


@require_GET
def whatsapp_generate_link_ajax(request):
    """AJAX endpoint to generate a WhatsApp link on-demand"""
    
    phone = request.GET.get('phone')
    message = request.GET.get('message')
    use_web = request.GET.get('use_web', 'false') == 'true'
    
    if not phone:
        return JsonResponse({'error': 'Phone number required'}, status=400)
    
    try:
        whatsapp_link = WhatsAppUtils.generate_chat_link(
            phone,
            message,
            use_web
        )
        
        return JsonResponse({
            'success': True,
            'whatsapp_link': whatsapp_link,
            'phone': WhatsAppUtils.clean_phone_number(phone),
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# ==============================================================================
# COURSE GROUP CRUD VIEWS
# ==============================================================================
from .forms import CourseGroupForm, CourseGroupScheduleFormSet

@require_http_methods(['GET', 'POST'])
def course_group_create(request):
    """Create a new course group (class)"""
    if request.method == 'POST':
        form = CourseGroupForm(request.POST)
        formset = CourseGroupScheduleFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                course = form.save()
                formset.instance = course
                formset.save()
            messages.success(request, f"Le groupe '{course.name}' a été créé avec succès.")
            return redirect('core:courses_list')
    else:
        form = CourseGroupForm()
        formset = CourseGroupScheduleFormSet()
    
    return render(request, 'core/course_group_form.html', {
        'form': form,
        'formset': formset,
        'title': 'Ajouter un groupe',
        'button_text': 'Créer le groupe'
    })


@require_http_methods(['GET', 'POST'])
def course_group_edit(request, group_id):
    """Edit an existing course group (class)"""
    course = get_object_or_404(CourseGroup, pk=group_id)
    if request.method == 'POST':
        form = CourseGroupForm(request.POST, instance=course)
        formset = CourseGroupScheduleFormSet(request.POST, instance=course)
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                form.save()
                formset.save()
            messages.success(request, f"Le groupe '{course.name}' a été mis à jour avec succès.")
            return redirect('core:courses_list')
    else:
        form = CourseGroupForm(instance=course)
        formset = CourseGroupScheduleFormSet(instance=course)
    
    return render(request, 'core/course_group_form.html', {
        'form': form,
        'formset': formset,
        'course': course,
        'title': f"Modifier le groupe : {course.name}",
        'button_text': 'Mettre à jour'
    })


@require_http_methods(['GET', 'POST'])
def course_group_delete_confirm(request, group_id):
    """Confirmation page before deleting a course group"""
    course = get_object_or_404(CourseGroup, pk=group_id)
    enrollment_count = course.enrollment_set.count()
    session_count = course.sessions.count()
    attendance_count = Attendance.objects.filter(course_group=course).count()
    
    return render(request, 'core/course_group_delete_confirm.html', {
        'course': course,
        'enrollment_count': enrollment_count,
        'session_count': session_count,
        'attendance_count': attendance_count,
    })


@require_POST
def course_group_delete(request, group_id):
    """Perform deletion of a course group"""
    course = get_object_or_404(CourseGroup, pk=group_id)
    name = course.name
    course.delete()
    messages.success(request, f"Le groupe '{name}' a été supprimé avec succès.")
    return redirect('core:courses_list')


from django.contrib.admin.views.decorators import staff_member_required

@staff_member_required
def admin_kpis_api(request):
    """
    API endpoint for admin dashboard KPI counts
    """
    students_count = Student.objects.filter(is_active=True).count()
    teachers_count = Teacher.objects.filter(is_active=True).count()
    groups_count = CourseGroup.objects.filter(is_active=True).count()
    payments_count = Payment.objects.count()
    sessions_count = Session.objects.count()
    rooms_count = Room.objects.filter(is_active=True).count()
    
    return JsonResponse({
        'students': students_count,
        'teachers': teachers_count,
        'groups': groups_count,
        'payments': payments_count,
        'sessions': sessions_count,
        'rooms': rooms_count,
    })


def schedule_conflicts(request):
    """
    Dashboard displaying all schedule, session, and capacity conflicts
    """
    from .utils import detect_all_conflicts
    conflicts_data = detect_all_conflicts()
    return render(request, 'core/schedule_conflicts.html', conflicts_data)


def check_conflict_ajax(request):
    """
    AJAX endpoint for real-time conflict checking on forms
    """
    from .models import Room, Teacher, CourseGroup, Session, CourseGroupSchedule
    from datetime import datetime as dt
    
    check_type = request.GET.get('type')  # 'session' or 'schedule'
    room_id = request.GET.get('room_id')
    teacher_id = request.GET.get('teacher_id')
    start_time_str = request.GET.get('start_time')
    end_time_str = request.GET.get('end_time')
    exclude_id = request.GET.get('exclude_id')
    
    if not (room_id and start_time_str and end_time_str):
        return JsonResponse({'conflicts': [], 'has_conflict': False})
        
    try:
        start_time = dt.strptime(start_time_str, '%H:%M').time()
        end_time = dt.strptime(end_time_str, '%H:%M').time()
    except ValueError:
        return JsonResponse({'error': 'Format de l\'heure invalide. Utilisez HH:MM.'}, status=400)
        
    if end_time <= start_time:
        return JsonResponse({'error': 'L\'heure de fin doit être postérieure à l\'heure de début.'}, status=400)
        
    conflicts = []
    
    if check_type == 'schedule':
        day = request.GET.get('day')
        if not day:
            return JsonResponse({'error': 'Le jour (day) est requis pour les schedules.'}, status=400)
            
        # Check room conflicts
        room_conflicts = CourseGroupSchedule.objects.filter(
            room_id=room_id,
            day=day,
            course_group__is_active=True
        )
        if exclude_id:
            room_conflicts = room_conflicts.exclude(id=exclude_id)
            
        for sch in room_conflicts:
            if (start_time < sch.end_time and end_time > sch.start_time):
                conflicts.append({
                    'type': 'ROOM',
                    'message': f"La salle est déjà réservée par le groupe '{sch.course_group.name}' de {sch.start_time.strftime('%H:%M')} à {sch.end_time.strftime('%H:%M')}."
                })
                
        # Check teacher conflicts
        if teacher_id:
            teacher_conflicts = CourseGroupSchedule.objects.filter(
                course_group__teacher_id=teacher_id,
                day=day,
                course_group__is_active=True
            )
            if exclude_id:
                teacher_conflicts = teacher_conflicts.exclude(id=exclude_id)
                
            for sch in teacher_conflicts:
                if (start_time < sch.end_time and end_time > sch.start_time):
                    conflicts.append({
                        'type': 'TEACHER',
                        'message': f"Le professeur est déjà affecté au groupe '{sch.course_group.name}' de {sch.start_time.strftime('%H:%M')} à {sch.end_time.strftime('%H:%M')}."
                    })
                    
    else:  # session check
        date_str = request.GET.get('date')
        if not date_str:
            return JsonResponse({'error': 'La date est requise pour les sessions.'}, status=400)
            
        try:
            date_obj = dt.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return JsonResponse({'error': 'Format de date invalide.'}, status=400)
            
        # Check room conflicts
        room_conflicts = Session.objects.filter(
            date=date_obj,
            room_id=room_id
        ).exclude(status='CANCELLED')
        if exclude_id:
            room_conflicts = room_conflicts.exclude(id=exclude_id)
            
        for s in room_conflicts:
            if (start_time < s.end_time and end_time > s.start_time):
                conflicts.append({
                    'type': 'ROOM',
                    'message': f"La salle est déjà réservée par le groupe '{s.group.name}' de {s.start_time.strftime('%H:%M')} à {s.end_time.strftime('%H:%M')}."
                })
                
        # Check teacher conflicts (considering group's primary or substitute teacher overlap)
        if teacher_id:
            teacher_conflicts = Session.objects.filter(
                date=date_obj
            ).filter(
                Q(group__teacher_id=teacher_id, substitute_teacher__isnull=True) |
                Q(substitute_teacher_id=teacher_id)
            ).exclude(status='CANCELLED')
            if exclude_id:
                teacher_conflicts = teacher_conflicts.exclude(id=exclude_id)
                
            for s in teacher_conflicts:
                if (start_time < s.end_time and end_time > s.start_time):
                    conflicts.append({
                        'type': 'TEACHER',
                        'message': f"Le professeur est déjà affecté au groupe '{s.group.name}' de {s.start_time.strftime('%H:%M')} à {s.end_time.strftime('%H:%M')}."
                    })
                    
    # Also check capacity and group conflicts if group_id is provided
    group_id = request.GET.get('group_id')
    if group_id:
        group = CourseGroup.objects.filter(id=group_id).first()
        room = Room.objects.filter(id=room_id).first()
        if group and room:
            student_count = group.students.filter(is_active=True).count()
            if student_count > room.capacity:
                conflicts.append({
                    'type': 'CAPACITY',
                    'message': f"Le nombre d'élèves inscrits ({student_count}) dépasse la capacité de la salle '{room.name}' ({room.capacity} places)."
                })
        
        # Check group overlaps
        group_conflicts = Session.objects.filter(
            date=date_obj,
            group_id=group_id
        ).exclude(status='CANCELLED')
        if exclude_id:
            group_conflicts = group_conflicts.exclude(id=exclude_id)
            
        for s in group_conflicts:
            if (start_time < s.end_time and end_time > s.start_time):
                conflicts.append({
                    'type': 'GROUP',
                    'message': f"Le groupe '{s.group.name}' a déjà une session planifiée de {s.start_time.strftime('%H:%M')} à {s.end_time.strftime('%H:%M')}."
                })
                
    return JsonResponse({
        'has_conflict': len(conflicts) > 0,
        'conflicts': conflicts
    })


    


# ==================== LEVELS CRUD ====================

def levels_list(request):
    """List all levels"""
    from .models import Level
    levels = Level.objects.all().order_by('order', 'name')
    return render(request, 'core/levels_list.html', {'levels': levels})

def level_detail(request, level_id):
    """Detail page for a specific academic level"""
    from .models import Level
    level = get_object_or_404(Level, pk=level_id)
    
    # Get course groups for this level, optimized with teacher prefetch
    course_groups = level.course_groups.all().select_related('teacher').prefetch_related('schedules__room')
    course_groups = course_groups.annotate(enrollment_count=Count('enrollment'))
    
    # Get students in this level
    students = level.students.all().prefetch_related('enrollment_set__course_group', 'payments').order_by('name')
    
    # Compute quick stats
    total_students = students.count()
    active_students = students.filter(is_active=True).count()
    total_groups = course_groups.count()
    
    # Total monthly expected revenue from this level
    total_monthly_fees = sum((s.total_monthly_fees() for s in students.filter(is_active=True)), Decimal('0.00'))
    
    context = {
        'level': level,
        'course_groups': course_groups,
        'students': students,
        'total_students': total_students,
        'active_students': active_students,
        'total_groups': total_groups,
        'total_monthly_fees': total_monthly_fees,
    }
    return render(request, 'core/level_detail.html', context)


@require_http_methods(['GET', 'POST'])
def level_create(request):
    """Create a new level"""
    from .forms import LevelForm
    if request.method == 'POST':
        form = LevelForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Niveau créé avec succès.")
            return redirect('core:levels_list')
    else:
        form = LevelForm()
    return render(request, 'core/level_form.html', {'form': form, 'action': 'Créer'})

@require_http_methods(['GET', 'POST'])
def level_edit(request, level_id):
    """Edit an existing level"""
    from .models import Level
    from .forms import LevelForm
    level = get_object_or_404(Level, pk=level_id)
    if request.method == 'POST':
        form = LevelForm(request.POST, instance=level)
        if form.is_valid():
            form.save()
            messages.success(request, "Niveau modifié avec succès.")
            return redirect('core:levels_list')
    else:
        form = LevelForm(instance=level)
    return render(request, 'core/level_form.html', {'form': form, 'action': 'Modifier', 'level': level})

def level_delete_confirm(request, level_id):
    """Confirmation page for deleting a level"""
    from .models import Level
    level = get_object_or_404(Level, pk=level_id)
    return render(request, 'core/level_delete_confirm.html', {'level': level})

@require_POST
def level_delete(request, level_id):
    """Delete a level"""
    from .models import Level
    from django.db.models import ProtectedError
    level = get_object_or_404(Level, pk=level_id)
    try:
        level.delete()
        messages.success(request, "Niveau supprimé avec succès.")
    except ProtectedError:
        messages.error(request, "Impossible de supprimer ce niveau car il est utilisé par des groupes de cours.")
    return redirect('core:levels_list')
