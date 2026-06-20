from django.db import models
from django.core.validators import MinValueValidator
from decimal import Decimal
from django.utils import timezone
from django.db.models import Sum
from django.core.exceptions import ValidationError

class Room(models.Model):
    """Salle de classe"""
    name = models.CharField(max_length=50, unique=True, verbose_name="Nom de la salle")
    capacity = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        verbose_name="Capacité"
    )
    is_active = models.BooleanField(default=True, verbose_name="Active")
    
    class Meta:
        verbose_name = "Salle"
        verbose_name_plural = "Salles"
        ordering = ['name']
    
    def __str__(self):
        return f"{self.name} ({self.capacity} places)"


class Teacher(models.Model):
    """Professeur"""
    PAYMENT_METHOD_CHOICES = [
        ('HOURLY', 'Taux horaire'),
        ('PERCENTAGE', 'Part des gains (pourcentage des gains de la classe)'),
        ('SESSION', 'Tarif par session'),
    ]
    
    name = models.CharField(max_length=100, verbose_name="Nom complet")
    phone = models.CharField(max_length=20, verbose_name="Téléphone")
    email = models.EmailField(blank=True, verbose_name="Email")
    hourly_rate = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name="Tarif horaire (DH)",
        default=Decimal('100.00'),
        blank=True,
        null=True
    )
    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        default='PERCENTAGE',
        verbose_name="Mode de paiement"
    )
    payment_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('50.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name="Part des gains (%)",
        blank=True,
        null=True
    )
    session_rate = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal('100.00'),
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name="Tarif par session (DH)",
        blank=True,
        null=True
    )
    is_active = models.BooleanField(default=True, verbose_name="Actif")
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = "Professeur"
        verbose_name_plural = "Professeurs"
        ordering = ['name']
    
    def clean(self):
        super().clean()
        if self.payment_method == 'HOURLY' and not self.hourly_rate:
            raise ValidationError({'hourly_rate': "Le tarif horaire est requis pour le mode de paiement 'Taux horaire'."})
        if self.payment_method == 'PERCENTAGE' and self.payment_percentage is None:
            raise ValidationError({'payment_percentage': "La part des gains (%) est requise pour le mode de paiement 'Part des gains'."})
        if self.payment_method == 'SESSION' and not self.session_rate:
            raise ValidationError({'session_rate': "Le tarif par session est requis pour le mode de paiement 'Tarif par session'."})
            
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
        
    def __str__(self):
        if self.payment_method == 'PERCENTAGE':
            return f"{self.name} ({self.payment_percentage}%)"
        elif self.payment_method == 'SESSION':
            return f"{self.name} ({self.session_rate} DH/sess)"
        return f"{self.name} ({self.hourly_rate} DH/h)"


class Level(models.Model):
    """Niveau académique"""
    name = models.CharField(max_length=100, unique=True, verbose_name="Nom du niveau")
    order = models.PositiveIntegerField(default=0, verbose_name="Ordre d'affichage")
    
    class Meta:
        verbose_name = "Niveau"
        verbose_name_plural = "Niveaux"
        ordering = ['order', 'name']
    
    def __str__(self):
        return self.name


class CourseGroup(models.Model):
    """Groupe de cours"""
    DAYS_CHOICES = [
        ('MON', 'Lundi'),
        ('TUE', 'Mardi'),
        ('WED', 'Mercredi'),
        ('THU', 'Jeudi'),
        ('FRI', 'Vendredi'),
        ('SAT', 'Samedi'),
        ('SUN', 'Dimanche'),
    ]
    
    name = models.CharField(max_length=100, verbose_name="Nom du groupe")
    subject = models.CharField(max_length=100, verbose_name="Matière")
    level = models.ForeignKey(
        Level,
        on_delete=models.SET_NULL,
        related_name='course_groups',
        verbose_name="Niveau",
        null=True,
        blank=True
    )
    
    monthly_price = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name="Prix mensuel (DH)"
    )
    
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.PROTECT,
        related_name='course_groups',
        verbose_name="Professeur"
    )
    
    is_active = models.BooleanField(default=True, verbose_name="Actif")
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = "Groupe de cours"
        verbose_name_plural = "Groupes de cours"
        ordering = ['name']
    
    def __str__(self):
        return f"{self.name} ({self.subject})"


class CourseGroupSchedule(models.Model):
    """Horaire hebdomadaire pour un groupe de cours"""
    course_group = models.ForeignKey(
        CourseGroup,
        on_delete=models.CASCADE,
        related_name='schedules',
        verbose_name="Groupe de cours"
    )
    day = models.CharField(
        max_length=3,
        choices=CourseGroup.DAYS_CHOICES,
        verbose_name="Jour"
    )
    start_time = models.TimeField(verbose_name="Heure de début")
    end_time = models.TimeField(verbose_name="Heure de fin")
    room = models.ForeignKey(
        Room,
        on_delete=models.PROTECT,
        related_name='schedules',
        verbose_name="Salle"
    )

    class Meta:
        verbose_name = "Horaire de groupe"
        verbose_name_plural = "Horaires de groupe"
        ordering = ['day', 'start_time']

    def __str__(self):
        return f"{self.course_group.name} - {self.get_day_display()} {self.start_time.strftime('%H:%M')}-{self.end_time.strftime('%H:%M')} ({self.room.name})"

    def duration_hours(self):
        from datetime import datetime
        start = datetime.combine(datetime.today(), self.start_time)
        end = datetime.combine(datetime.today(), self.end_time)
        return (end - start).total_seconds() / 3600

    def clean(self):

        cleaned_data = super().clean()

        # Sometimes super().clean() may return None
        if cleaned_data is None:
            return {}
            
        if self.end_time <= self.start_time:
            raise ValidationError("L'heure de fin doit être postérieure à l'heure de début.")
        
        # Check room conflicts
        overlapping_rooms = CourseGroupSchedule.objects.filter(
            room=self.room,
            day=self.day,
            course_group__is_active=True
        )
        if self.pk:
            overlapping_rooms = overlapping_rooms.exclude(pk=self.pk)
        for s in overlapping_rooms:
            if (self.start_time < s.end_time and self.end_time > s.start_time):
                raise ValidationError(
                    f"La salle '{self.room.name}' est déjà réservée par le groupe '{s.course_group.name}' "
                    f"de {s.start_time.strftime('%H:%M')} à {s.end_time.strftime('%H:%M')} le {s.get_day_display()}."
                )

        # Check teacher conflicts
        overlapping_teachers = CourseGroupSchedule.objects.filter(
            course_group__teacher=self.course_group.teacher,
            day=self.day,
            course_group__is_active=True
        )
        if self.pk:
            overlapping_teachers = overlapping_teachers.exclude(pk=self.pk)
        for s in overlapping_teachers:
            if (self.start_time < s.end_time and self.end_time > s.start_time):
                raise ValidationError(
                    f"Le professeur '{self.course_group.teacher.name}' est déjà affecté au groupe '{s.course_group.name}' "
                    f"de {s.start_time.strftime('%H:%M')} à {s.end_time.strftime('%H:%M')} le {s.get_day_display()}."
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class Student(models.Model):
    """Élève"""
    name = models.CharField(max_length=100, verbose_name="Nom complet")
    phone = models.CharField(max_length=20, blank=True, verbose_name="Téléphone élève")
    parent_contact = models.CharField(max_length=20, verbose_name="Téléphone parent")
    parent_name = models.CharField(max_length=100, blank=True, verbose_name="Nom du parent")
    
    address = models.TextField(blank=True, verbose_name="Adresse")
    date_of_birth = models.DateField(null=True, blank=True, verbose_name="Date de naissance")
    
    level = models.ForeignKey(
        Level,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='students',
        verbose_name="Niveau scolaire"
    )
    
    # Relation Many-to-Many avec les groupes
    enrollments = models.ManyToManyField(
        CourseGroup,
        through='Enrollment',
        related_name='students',
        verbose_name="Groupes inscrits"
    )
    
    is_active = models.BooleanField(default=True, verbose_name="Actif")
    created_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, verbose_name="Notes")
    
    class Meta:
        verbose_name = "Élève"
        verbose_name_plural = "Élèves"
        ordering = ['name']
    
    def __str__(self):
        return self.name
    
    def total_monthly_fees(self):
        """Calcule le total des frais mensuels"""
        active_enrollments = self.enrollment_set.filter(is_active=True)
        # Ensure Decimal result even when no enrollments
        total = sum((e.course_group.monthly_price for e in active_enrollments), Decimal('0.00'))
        return total
     
    def payment_status(self):
        from .utils import calculate_student_monthly_total
        current_month = timezone.now().date().replace(day=1)

        required = calculate_student_monthly_total(self)

        paid = (
            self.payments
            .filter(month_covered=current_month, status='PAID')
            .aggregate(total=Sum('amount'))['total']
            or Decimal('0')
        )

        if required == 0:
            return 'OK'  # No courses = nothing to pay

        if paid >= required:
            return 'OK'
        elif paid > 0:
            return 'PARTIAL'
        return 'UNPAID'




class Enrollment(models.Model):
    """Inscription d'un élève dans un groupe (table intermédiaire)"""
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    course_group = models.ForeignKey(CourseGroup, on_delete=models.CASCADE)
    enrolled_date = models.DateField(auto_now_add=True, verbose_name="Date d'inscription")
    is_active = models.BooleanField(default=True, verbose_name="Active")
    next_payment_date = models.DateField(null=True, blank=True, verbose_name="Prochaine date de paiement")
    
    class Meta:
        verbose_name = "Inscription"
        verbose_name_plural = "Inscriptions"
        unique_together = [['student', 'course_group']]
    
    def get_initial_payment(self):
        """Returns the initial payment for this enrollment's month, if paid"""
        month_start = self.enrolled_date.replace(day=1)
        payment = self.student.payments.filter(month_covered=month_start, status='PAID').first()
        if payment:
            return f"{payment.amount} DH (Reçu N° {payment.receipt_number})"
        return "Non payé"

    def __str__(self):
        return f"{self.student.name} → {self.course_group.name}"


class Payment(models.Model):
    """Paiement"""
    STATUS_CHOICES = [
        ('PAID', 'Payé'),
        ('PENDING', 'En attente'),
        ('CANCELLED', 'Annulé'),
    ]
    
    PAYMENT_METHOD_CHOICES = [
        ('CASH', 'Espèces'),
        ('TRANSFER', 'Virement'),
        ('CHECK', 'Chèque'),
    ]
    
    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name='payments',
        verbose_name="Élève"
    )
    
    amount = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name="Montant (DH)"
    )
    
    payment_date = models.DateField(verbose_name="Date de paiement")
    month_covered = models.DateField(
        verbose_name="Mois couvert",
        help_text="Premier jour du mois couvert par ce paiement"
    )
    
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='PAID',
        verbose_name="Statut"
    )
    
    payment_method = models.CharField(
        max_length=10,
        choices=PAYMENT_METHOD_CHOICES,
        default='CASH',
        verbose_name="Mode de paiement"
    )
    
    receipt_number = models.CharField(
        max_length=50,
        unique=True,
        verbose_name="N° de reçu"
    )
    
    notes = models.TextField(blank=True, verbose_name="Notes")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.CharField(max_length=100, blank=True, verbose_name="Créé par")
    
    # Verrou numérique : empêcher modification
    is_locked = models.BooleanField(default=False, verbose_name="Verrouillé")
    
    class Meta:
        verbose_name = "Paiement"
        verbose_name_plural = "Paiements"
        ordering = ['-payment_date', '-created_at']
    
    def __str__(self):
        return f"Reçu {self.receipt_number} - {self.student.name} - {self.amount} DH"
    
    def get_prorated_details(self):
        """Returns pro-rated details for student enrollments in the payment month"""
        from .utils import count_scheduled_sessions_in_month, count_remaining_sessions_in_month
        details = []
        month_covered = self.month_covered
        if not month_covered:
            return details
            
        enrollments = self.student.enrollment_set.filter(is_active=True).select_related('course_group')
        for e in enrollments:
            if e.enrolled_date.year == month_covered.year and e.enrolled_date.month == month_covered.month:
                if e.enrolled_date.day > 1:
                    total_sess = count_scheduled_sessions_in_month(e.course_group, month_covered.year, month_covered.month)
                    rem_sess = count_remaining_sessions_in_month(e.course_group, e.enrolled_date)
                    if total_sess > 0:
                        sess_price = (e.course_group.monthly_price / Decimal(total_sess)).quantize(Decimal('0.01'))
                        prorated_price = (Decimal(rem_sess) * sess_price).quantize(Decimal('0.01'))
                    else:
                        sess_price = Decimal('0.00')
                        prorated_price = Decimal('0.00')
                    details.append({
                        'course_group': e.course_group,
                        'total_sessions': total_sess,
                        'remaining_sessions': rem_sess,
                        'session_price': sess_price,
                        'prorated_price': prorated_price
                    })
        return details
    
    def save(self, *args, **kwargs):
        # Générer automatiquement le numéro de reçu
        if self.month_covered:
            self.month_covered = self.month_covered.replace(day=1)

        if not self.receipt_number:
            year = timezone.now().year
            last_payment = Payment.objects.filter(
                receipt_number__startswith=f"REC{year}"
            ).order_by('-receipt_number').first()
            
            if last_payment:
                last_num = int(last_payment.receipt_number[-4:])
                new_num = last_num + 1
            else:
                new_num = 1
            
            self.receipt_number = f"REC{year}{new_num:04d}"
        
        super().save(*args, **kwargs)


class Attendance(models.Model):
    """Présence aux cours"""
    student = models.ForeignKey(Student, on_delete=models.CASCADE, verbose_name="Élève")
    course_group = models.ForeignKey(CourseGroup, on_delete=models.CASCADE, verbose_name="Groupe")
    date = models.DateField(verbose_name="Date")
    is_present = models.BooleanField(default=True, verbose_name="Présent")
    notes = models.TextField(blank=True, verbose_name="Notes")
    
    class Meta:
        verbose_name = "Présence"
        verbose_name_plural = "Présences"
        unique_together = [['student', 'course_group', 'date']]
        ordering = ['-date']
    
    def __str__(self):
        status = "✓" if self.is_present else "✗"
        return f"{status} {self.student.name} - {self.course_group.name} - {self.date}"


class Session(models.Model):
    """Instance of a group meeting (used for scheduling & payroll)"""
    STATUS_CHOICES = [
        ('PLANNED', 'Prévu'),
        ('DONE', 'Terminé'),
        ('CANCELLED', 'Annulé'),
    ]

    group = models.ForeignKey(CourseGroup, on_delete=models.CASCADE, related_name='sessions')
    schedule = models.ForeignKey(CourseGroupSchedule, on_delete=models.SET_NULL, null=True, blank=True, related_name='sessions')
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    room = models.ForeignKey(
        Room,
        on_delete=models.PROTECT,
        related_name='sessions',
        verbose_name='Salle'
    )
    substitute_teacher = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='substitute_sessions',
        verbose_name="Enseignant remplaçant"
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PLANNED')
    notes = models.TextField(blank=True)
    is_manually_edited = models.BooleanField(default=False, verbose_name="Modifié manuellement")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Session'
        verbose_name_plural = 'Sessions'
        ordering = ['-date', 'start_time']
        indexes = [
            models.Index(fields=['date']),
        ]

    def __str__(self):
        return f"{self.group.name} - {self.date} {self.start_time.strftime('%H:%M')}-{self.end_time.strftime('%H:%M')}"

    def clean(self):
        from django.db.models import Q
        if self.end_time <= self.start_time:
            raise ValidationError('End time must be after start time')
        
        if not self.room_id:
            raise ValidationError('La salle est requise.')

        # 1. Check room conflicts (excluding CANCELLED sessions)
        room_conflicts = Session.objects.filter(date=self.date, room=self.room).exclude(status='CANCELLED')
        if self.pk:
            room_conflicts = room_conflicts.exclude(pk=self.pk)

        for s in room_conflicts:
            if (self.start_time < s.end_time and self.end_time > s.start_time):
                raise ValidationError(
                    f"La salle '{self.room.name}' est déjà réservée par le groupe '{s.group.name}' "
                    f"de {s.start_time.strftime('%H:%M')} à {s.end_time.strftime('%H:%M')}."
                )

        # 2. Check teacher conflicts (excluding CANCELLED sessions)
        effective_teacher = getattr(self, 'substitute_teacher', None) or (self.group.teacher if self.group else None)
        if effective_teacher:
            teacher_conflicts = Session.objects.filter(date=self.date).exclude(status='CANCELLED')
            if self.pk:
                teacher_conflicts = teacher_conflicts.exclude(pk=self.pk)
            # Filter where the teacher is either the substitute_teacher or (substitute_teacher is null and primary teacher is effective_teacher)
            teacher_conflicts = teacher_conflicts.filter(
                Q(substitute_teacher=effective_teacher) |
                Q(substitute_teacher__isnull=True, group__teacher=effective_teacher)
            )
            for s in teacher_conflicts:
                if (self.start_time < s.end_time and self.end_time > s.start_time):
                    raise ValidationError(
                        f"Le professeur '{effective_teacher.name}' est déjà affecté au groupe '{s.group.name}' "
                        f"de {s.start_time.strftime('%H:%M')} à {s.end_time.strftime('%H:%M')}."
                    )

        # 3. Check group conflicts (excluding CANCELLED sessions)
        if self.group:
            group_conflicts = Session.objects.filter(date=self.date, group=self.group).exclude(status='CANCELLED')
            if self.pk:
                group_conflicts = group_conflicts.exclude(pk=self.pk)
            for s in group_conflicts:
                if (self.start_time < s.end_time and self.end_time > s.start_time):
                    raise ValidationError(
                        f"Le groupe '{self.group.name}' a déjà une session planifiée de "
                        f"{s.start_time.strftime('%H:%M')} à {s.end_time.strftime('%H:%M')}."
                    )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def duration_hours(self):
        from datetime import datetime
        start = datetime.combine(datetime.today(), self.start_time)
        end = datetime.combine(datetime.today(), self.end_time)
        return (end - start).total_seconds() / 3600


class SessionException(models.Model):
    """Per-date exception / override for a CourseGroup's regular session.

    Use this to cancel a particular occurrence or to move it to another time/room.
    """
    course_group = models.ForeignKey(CourseGroup, on_delete=models.CASCADE, related_name='exceptions')
    date = models.DateField()

    # If True, this occurrence is cancelled (no session will be generated)
    cancelled = models.BooleanField(default=False)

    # Optional overrides — if provided they replace the group's default for that date
    override_room = models.ForeignKey(Room, null=True, blank=True, on_delete=models.PROTECT)
    override_start_time = models.TimeField(null=True, blank=True)
    override_end_time = models.TimeField(null=True, blank=True)

    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [['course_group', 'date']]
        verbose_name = 'Exception de session'
        verbose_name_plural = 'Exceptions de sessions'
        ordering = ['-date']

    def __str__(self):
        flag = 'CANCELLED' if self.cancelled else 'OVERRIDE' if (self.override_start_time or self.override_end_time or self.override_room) else 'NOTE'
        return f"{self.course_group.name} - {self.date} ({flag})"

    def clean(self):
        """
        Validate SessionException overrides:
        - If override times are provided, both start and end must be present and end > start.
        - A cancelled exception should not include overrides.
        - Optionally pre-check for simple room/teacher conflicts on the target date when an override time/room is provided.
        """
        errors = []

        # Validate override times presence/ordering
        if (self.override_start_time and not self.override_end_time) or (self.override_end_time and not self.override_start_time):
            errors.append('Les heures de début et de fin doivent être fournies ensemble pour une modification (override).')

        if self.override_start_time and self.override_end_time:
            if self.override_end_time <= self.override_start_time:
                errors.append('L\'heure de fin doit être postérieure à l\'heure de début pour l\'override.')

        # Cancelled exceptions should not carry overrides
        if self.cancelled and (self.override_room or self.override_start_time or self.override_end_time):
            errors.append('Une exception annulée ne doit pas contenir d\'overrides (salle/horaires).')

        # Pre-check simple conflicts only when overrides specify a concrete time window
        if not self.cancelled and self.override_start_time and self.override_end_time:
            # Room conflicts
            target_room = self.override_room
            if target_room:
                room_qs = Session.objects.filter(date=self.date, room=target_room).exclude(status='CANCELLED')
                # Exclude sessions belonging to the same course_group (they are the ones we are overriding)
                room_qs = room_qs.exclude(group=self.course_group)
                for s in room_qs:
                    if (self.override_start_time < s.end_time and self.override_end_time > s.start_time):
                        errors.append(
                            f"Conflit de salle avec le groupe '{s.group.name}' ({s.start_time.strftime('%H:%M')}-{s.end_time.strftime('%H:%M')})."
                        )
                        break

            # Teacher conflicts
            teacher = None
            try:
                teacher = self.course_group.teacher
            except Exception:
                teacher = None

            if teacher:
                teach_qs = Session.objects.filter(date=self.date, group__teacher=teacher).exclude(status='CANCELLED')
                teach_qs = teach_qs.exclude(group=self.course_group)
                for s in teach_qs:
                    if (self.override_start_time < s.end_time and self.override_end_time > s.start_time):
                        errors.append(
                            f"Conflit de professeur avec le groupe '{s.group.name}' ({s.start_time.strftime('%H:%M')}-{s.end_time.strftime('%H:%M')})."
                        )
                        break

        if errors:
            raise ValidationError(errors)

    def effective_room(self, schedule_room):
        return self.override_room or schedule_room

    def effective_start(self, schedule_start):
        return self.override_start_time or schedule_start

    def effective_end(self, schedule_end):
        return self.override_end_time or schedule_end


# ==================== SIGNALS ====================
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.db.models import Max

@receiver(post_save, sender=CourseGroup)
def sync_course_group_on_save(sender, instance, **kwargs):
    """Automatically syncs future sessions when a CourseGroup is saved"""
    from .utils import generate_sessions_from_coursegroups
    from datetime import timedelta
    
    today = timezone.now().date()
    max_date = Session.objects.aggregate(Max('date'))['date__max']
    if not max_date or max_date < today:
        max_date = today + timedelta(weeks=4)
        
    generate_sessions_from_coursegroups(today, max_date, force=True, course=instance)


@receiver(post_save, sender=CourseGroupSchedule)
def sync_course_group_schedule_on_save(sender, instance, **kwargs):
    """Automatically syncs future sessions when a CourseGroupSchedule is saved"""
    from .utils import generate_sessions_from_coursegroups
    from datetime import timedelta
    from django.core.cache import cache
    
    today = timezone.now().date()
    max_date = Session.objects.aggregate(Max('date'))['date__max']
    if not max_date or max_date < today:
        max_date = today + timedelta(weeks=4)
        
    generate_sessions_from_coursegroups(today, max_date, force=True, course=instance.course_group)
    
    # Bust sidebar conflict badge cache so it reflects the new schedule immediately
    cache.delete('sidebar_conflict_count')


@receiver(post_delete, sender=CourseGroupSchedule)
def sync_course_group_schedule_on_delete(sender, instance, **kwargs):
    """Automatically syncs future sessions when a CourseGroupSchedule is deleted"""
    from .utils import generate_sessions_from_coursegroups
    from datetime import timedelta
    from django.core.cache import cache
    
    today = timezone.now().date()
    max_date = Session.objects.aggregate(Max('date'))['date__max']
    if not max_date or max_date < today:
        max_date = today + timedelta(weeks=4)
        
    generate_sessions_from_coursegroups(today, max_date, force=True, course=instance.course_group)
    
    # Bust sidebar conflict badge cache
    cache.delete('sidebar_conflict_count')


@receiver(post_delete, sender=Session)
def session_deleted_signal(sender, instance, **kwargs):
    """Creates a cancelled SessionException when a future planned session is deleted,
    so that subsequent session generations do not recreate it.
    """
    if instance.status == 'PLANNED' and instance.date >= timezone.now().date():
        SessionException.objects.get_or_create(
            course_group=instance.group,
            date=instance.date,
            defaults={'cancelled': True}
        )