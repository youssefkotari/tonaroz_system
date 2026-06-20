from django.test import TestCase
from datetime import date
from decimal import Decimal
from django.utils import timezone
from .models import Room, Teacher, CourseGroup, Student, Enrollment, Payment, CourseGroupSchedule
from .utils import calculate_enrollment_expected_fee, get_student_payment_status

class PaymentLogicTestCase(TestCase):
    def setUp(self):
        self.room = Room.objects.create(name="Salle 101", capacity=30)
        self.teacher = Teacher.objects.create(
            name="Teacher John", 
            phone="12345678", 
            payment_method="PERCENTAGE", 
            payment_percentage=Decimal('50.00')
        )
        self.course = CourseGroup.objects.create(
            name="Math 1",
            subject="Math",
            monthly_price=Decimal('1000.00'),
            teacher=self.teacher
        )
        self.schedule = CourseGroupSchedule.objects.create(
            course_group=self.course,
            day="MON",
            start_time="14:00:00",
            end_time="16:00:00",
            room=self.room
        )
        self.student = Student.objects.create(
            name="Student Alice",
            parent_contact="87654321"
        )

    def test_future_enrollment_expected_fee_is_zero(self):
        enrollment = Enrollment.objects.create(
            student=self.student,
            course_group=self.course,
            is_active=True
        )
        Enrollment.objects.filter(pk=enrollment.pk).update(enrolled_date=date(2026, 7, 15))
        enrollment.refresh_from_db()
        
        june_date = date(2026, 6, 1)
        expected_fee = calculate_enrollment_expected_fee(enrollment, june_date)
        self.assertEqual(expected_fee, Decimal('0.00'))

    def test_current_month_prorated_expected_fee(self):
        # Enrollment date mid-month: Oct 19, 2026 (Monday)
        # Mondays in October 2026: Oct 5, 12, 19, 26 (4 total)
        # Remaining from Oct 19: Oct 19, 26 (2 remaining)
        # Expected fee = 2/4 * 1000.00 = 500.00
        enrollment = Enrollment.objects.create(
            student=self.student,
            course_group=self.course,
            is_active=True
        )
        Enrollment.objects.filter(pk=enrollment.pk).update(enrolled_date=date(2026, 10, 19))
        enrollment.refresh_from_db()
        
        october_date = date(2026, 10, 1)
        expected_fee = calculate_enrollment_expected_fee(enrollment, october_date)
        self.assertEqual(expected_fee, Decimal('500.00'))

    def test_get_student_payment_status_historical_month(self):
        enrollment = Enrollment.objects.create(
            student=self.student,
            course_group=self.course,
            is_active=True
        )
        Enrollment.objects.filter(pk=enrollment.pk).update(enrolled_date=date(2026, 5, 1))
        enrollment.refresh_from_db()
        
        may_date = date(2026, 5, 1)
        status = get_student_payment_status(self.student, may_date)
        self.assertEqual(status['required'], Decimal('1000.00'))
        self.assertEqual(status['remaining'], Decimal('1000.00'))
        self.assertEqual(status['status'], 'UNPAID')
