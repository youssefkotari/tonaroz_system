from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.urls import path
from django.shortcuts import render, redirect
from django.contrib import messages
from django.utils import timezone
from import_export import resources, fields
from import_export.admin import ImportExportModelAdmin
from import_export.widgets import ForeignKeyWidget

from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.import_export.forms import ExportForm, ImportForm

from .models import Room, Teacher, CourseGroup, Student, Enrollment, Payment, Attendance, Session, SessionException, CourseGroupSchedule, Level
from django.core.exceptions import ValidationError


# ==================== RESOURCES (Import/Export) ====================

class RoomResource(resources.ModelResource):
    class Meta:
        model = Room
        fields = ('id', 'name', 'capacity', 'is_active')
        export_order = fields


class TeacherResource(resources.ModelResource):
    class Meta:
        model = Teacher
        fields = ('id', 'name', 'phone', 'email', 'hourly_rate', 'payment_method', 'payment_percentage', 'is_active')


class CourseGroupResource(resources.ModelResource):
    teacher = fields.Field(
        column_name='teacher',
        attribute='teacher',
        widget=ForeignKeyWidget(Teacher, 'name')
    )
    
    class Meta:
        model = CourseGroup
        fields = ('id', 'name', 'subject', 'level', 'monthly_price', 'teacher')


class StudentResource(resources.ModelResource):
    total_fees = fields.Field()
    payment_status = fields.Field()
    
    class Meta:
        model = Student
        fields = ('id', 'name', 'phone', 'parent_contact', 'parent_name', 
                  'address', 'is_active', 'total_fees', 'payment_status')
    
    def dehydrate_total_fees(self, student):
        return str(student.total_monthly_fees())
    
    def dehydrate_payment_status(self, student):
        return student.payment_status()


class PaymentResource(resources.ModelResource):
    student = fields.Field(
        column_name='student',
        attribute='student',
        widget=ForeignKeyWidget(Student, 'name')
    )
    
    class Meta:
        model = Payment
        fields = ('id', 'receipt_number', 'student', 'amount', 'payment_date',
                  'month_covered', 'status', 'payment_method', 'notes')


# ==================== INLINE ADMINS ====================

class EnrollmentInline(TabularInline):
    model = Enrollment
    extra = 1
    fields = ('course_group', 'enrolled_date', 'is_active')
    readonly_fields = ('enrolled_date',)
    autocomplete_fields = ['course_group']


class PaymentInline(TabularInline):
    model = Payment
    extra = 0
    fields = ('receipt_number', 'amount', 'payment_date', 'month_covered', 'status', 'payment_method')
    readonly_fields = ('receipt_number',)
    can_delete = False
    
    def has_add_permission(self, request, obj=None):
        return False


# ==================== CUSTOM FILTERS ====================

class PaymentStatusFilter(admin.SimpleListFilter):
    title = 'Statut de paiement'
    parameter_name = 'payment_status'
    
    def lookups(self, request, model_admin):
        return (
            ('ok', '✅ À jour'),
            ('partial', '🟠 Partiel'),
            ('unpaid', '🔴 Impayé'),
        )
    
    def queryset(self, request, queryset):
        if self.value():
            def normalize(s):
                return (s or '').strip().upper()
            wanted = self.value()
            filtered_ids = []
            for student in queryset:
                status = normalize(student.payment_status())
                if wanted == 'ok' and status in ('OK','PAID','UP_TO_DATE','À_JOUR','AJOUR'):
                    filtered_ids.append(student.id)
                elif wanted == 'partial' and status in ('PARTIAL','PARTIEL','PARTIALLY_PAID'):
                    filtered_ids.append(student.id)
                elif wanted == 'unpaid' and status in ('UNPAID','IMPAID','OVERDUE','DUE',''):
                    filtered_ids.append(student.id)
            return queryset.filter(id__in=filtered_ids)
        return queryset


class CurrentMonthPaymentFilter(admin.SimpleListFilter):
    title = 'Paiement du mois'
    parameter_name = 'current_month'
    
    def lookups(self, request, model_admin):
        return (
            ('yes', 'Payé ce mois'),
            ('no', 'Non payé ce mois'),
        )
    
    def queryset(self, request, queryset):
        current_month = timezone.now().date().replace(day=1)
        if self.value() == 'yes':
            return queryset.filter(month_covered=current_month, status='PAID')
        elif self.value() == 'no':
            paid_students = Payment.objects.filter(
                month_covered=current_month,
                status='PAID'
            ).values_list('student_id', flat=True)
            return queryset.exclude(student_id__in=paid_students)
        return queryset


# ==================== MAIN ADMIN CLASSES ====================

@admin.register(Room)
class RoomAdmin(ModelAdmin, ImportExportModelAdmin):
    resource_class = RoomResource
    import_form_class = ImportForm
    export_form_class = ExportForm
    list_display = ('name', 'capacity', 'active_status', 'course_count')
    list_filter = ('is_active',)
    search_fields = ('name',)
    
    def active_status(self, obj):
        if obj.is_active:
            return mark_safe('<span style="color: green;">✓ Active</span>')
        return mark_safe('<span style="color: red;">✗ Inactive</span>')
    active_status.short_description = 'Statut'
    
    def course_count(self, obj):
        count = CourseGroup.objects.filter(schedules__room=obj, is_active=True).distinct().count()
        return format_html('<strong>{}</strong> cours', count)
    course_count.short_description = 'Cours actifs'


@admin.register(Level)
class LevelAdmin(ModelAdmin, ImportExportModelAdmin):
    list_display = ('name', 'order', 'course_group_count', 'student_count')
    search_fields = ('name',)
    ordering = ('order', 'name')
    
    def course_group_count(self, obj):
        return obj.course_groups.count()
    course_group_count.short_description = 'Groupes de cours'
    
    def student_count(self, obj):
        return obj.students.count()
    student_count.short_description = 'Élèves'


@admin.register(Teacher)
class TeacherAdmin(ModelAdmin, ImportExportModelAdmin):
    resource_class = TeacherResource
    import_form_class = ImportForm
    export_form_class = ExportForm
    list_display = ('name', 'phone', 'payment_rate_display', 'course_count', 'active_status')
    list_filter = ('is_active', 'payment_method')
    search_fields = ('name', 'phone', 'email')
    readonly_fields = ('created_at',)
    
    class Media:
        js = ('admin/js/teacher_admin.js',)
        
    fieldsets = (
        ('Informations personnelles', {
            'fields': ('name', 'phone', 'email')
        }),
        ('Informations professionnelles', {
            'fields': ('payment_method', 'payment_percentage', 'hourly_rate', 'is_active', 'created_at')
        }),
    )
    
    def payment_rate_display(self, obj):
        if obj.payment_method == 'PERCENTAGE':
            return format_html('<strong>{}% (Gains)</strong>', obj.payment_percentage)
        return format_html('<strong>{} DH/h</strong>', obj.hourly_rate)
    payment_rate_display.short_description = 'Tarif / Mode'
    
    def course_count(self, obj):
        count = obj.course_groups.filter(is_active=True).count()
        if count > 0:
            return format_html('<span style="color: green;">{} groupes</span>', count)
        return mark_safe('<span style="color: gray;">0 groupe</span>')
    course_count.short_description = 'Groupes'
    
    def active_status(self, obj):
        if obj.is_active:
            return mark_safe('<span style="color: green;">✓</span>')
        return mark_safe('<span style="color: red;">✗</span>')
    active_status.short_description = 'Actif'


class CourseGroupScheduleInline(TabularInline):
    model = CourseGroupSchedule
    extra = 1
    autocomplete_fields = ['room']


@admin.register(CourseGroup)
class CourseGroupAdmin(ModelAdmin, ImportExportModelAdmin):
    resource_class = CourseGroupResource
    import_form_class = ImportForm
    export_form_class = ExportForm
    list_display = ('name', 'subject', 'level', 'schedules_display', 
                    'teacher', 'price_display', 'student_count', 'status_badge')
    list_filter = ('is_active', 'schedules__day', 'teacher', 'schedules__room', 'level')
    search_fields = ('name', 'subject', 'level')
    autocomplete_fields = ['teacher']
    inlines = [CourseGroupScheduleInline]
    
    fieldsets = (
        ('Informations générales', {
            'fields': ('name', 'subject', 'level', 'monthly_price')
        }),
        ('Assignation', {
            'fields': ('teacher',)
        }),
        ('Statut', {
            'fields': ('is_active',)
        }),
    )
    
    def schedules_display(self, obj):
        schedules = obj.schedules.all()
        if not schedules.exists():
            return mark_safe('<span style="color: gray;">Aucun horaire</span>')
        html_lines = []
        for sch in schedules:
            html_lines.append(f"<strong>{sch.get_day_display()}</strong>: {sch.start_time.strftime('%H:%M')}-{sch.end_time.strftime('%H:%M')} ({sch.room.name})")
        return mark_safe('<br>'.join(html_lines))
    schedules_display.short_description = 'Horaires'
    
    def price_display(self, obj):
        return format_html('<strong>{} DH</strong>/mois', obj.monthly_price)
    price_display.short_description = 'Prix'
    
    def student_count(self, obj):
        count = obj.students.filter(is_active=True).count()
        schedules = obj.schedules.all()
        if schedules.exists():
            min_capacity = min(sch.room.capacity for sch in schedules)
            if count >= (min_capacity * 0.8):
                color = 'red'
            elif count >= (min_capacity * 0.5):
                color = 'orange'
            else:
                color = 'green'
            return format_html(
                '<span style="color: {};">{} (cap. min: {})</span>',
                color, count, min_capacity
            )
        return format_html('<strong>{}</strong>', count)
    student_count.short_description = 'Élèves'
    
    def status_badge(self, obj):
        if obj.is_active:
            return mark_safe('<span style="background: green; color: white; padding: 3px 8px; border-radius: 3px;">Actif</span>')
        return mark_safe('<span style="background: gray; color: white; padding: 3px 8px; border-radius: 3px;">Inactif</span>')
    status_badge.short_description = 'Statut'


@admin.register(Student)
class StudentAdmin(ModelAdmin, ImportExportModelAdmin):
    resource_class = StudentResource
    import_form_class = ImportForm
    export_form_class = ExportForm
    list_display = ('name', 'parent_contact', 'groups_display', 'monthly_fees_display', 
                    'payment_status_badge', 'active_badge')
    list_filter = ('is_active', PaymentStatusFilter, 'enrollment__course_group')
    search_fields = ('name', 'phone', 'parent_contact', 'parent_name')
    inlines = [EnrollmentInline, PaymentInline]
    
    fieldsets = (
        ('Informations élève', {
            'fields': ('name', 'phone', 'date_of_birth')
        }),
        ('Contact parent', {
            'fields': ('parent_name', 'parent_contact', 'address')
        }),
        ('Autres', {
            'fields': ('is_active', 'notes')
        }),
    )
    
    actions = ['generate_payment_reminders']
    
    def groups_display(self, obj):
        groups = obj.enrollment_set.filter(is_active=True)
        if groups.exists():
            group_list = '<br>'.join([f"• {e.course_group.name}" for e in groups[:3]])
            if groups.count() > 3:
                group_list += f'<br>... +{groups.count() - 3} autres'
            return mark_safe(group_list)
        return mark_safe('<span style="color: gray;">Aucun groupe</span>')
    groups_display.short_description = 'Groupes'
    
    def monthly_fees_display(self, obj):
        total = obj.total_monthly_fees()
        return format_html('<strong style="font-size: 14px;">{} DH</strong>', total)
    monthly_fees_display.short_description = 'Frais mensuels'
    
    def payment_status_badge(self, obj):
        status = (obj.payment_status() or '').strip().upper()
        if status in ('OK','PAID','UP_TO_DATE','À_JOUR','AJOUR'):
            return mark_safe('<span style="background: #28a745; color: white; padding: 4px 10px; border-radius: 4px; font-weight: bold;">✓ PAYÉ</span>')
        if status in ('PARTIAL','PARTIEL','PARTIALLY_PAID'):
            return mark_safe('<span style="background: #ff9800; color: white; padding: 4px 10px; border-radius: 4px; font-weight: bold;">⚠ PARTIEL</span>')
        if status in ('UNPAID','IMPAID','OVERDUE','DUE',''):
            return mark_safe('<span style="background: #dc3545; color: white; padding: 4px 10px; border-radius: 4px; font-weight: bold;">✗ IMPAYÉ</span>')
        # fallback: show raw normalized status
        return format_html('<span style="background: gray; color: white; padding: 4px 10px; border-radius: 4px; font-weight: bold;">{}</span>', status)
    payment_status_badge.short_description = 'Statut'

    
    def active_badge(self, obj):
        if obj.is_active:
            return mark_safe('<span style="color: green; font-size: 18px;">✓</span>')
        return mark_safe('<span style="color: red; font-size: 18px;">✗</span>')
    active_badge.short_description = 'Actif'
    
    def generate_payment_reminders(self, request, queryset):
        """Action pour générer des rappels de paiement"""
        unpaid = []
        for student in queryset:
            if student.payment_status() in ['UNPAID', 'PARTIAL']:
                unpaid.append(student.name)
        
        if unpaid:
            messages.warning(
                request,
                f"📱 {len(unpaid)} élèves à relancer : {', '.join(unpaid[:5])}" +
                (f"... et {len(unpaid) - 5} autres" if len(unpaid) > 5 else "")
            )
        else:
            messages.success(request, "✅ Tous les élèves sélectionnés sont à jour !")
    
    generate_payment_reminders.short_description = "📱 Générer rappels de paiement"


@admin.register(Payment)
class PaymentAdmin(ModelAdmin, ImportExportModelAdmin):
    resource_class = PaymentResource
    import_form_class = ImportForm
    export_form_class = ExportForm
    list_display = ('receipt_number', 'student', 'amount_display', 'payment_date', 
                    'month_covered', 'status_badge', 'payment_method', 'locked_status')
    list_filter = ('status', 'payment_method', CurrentMonthPaymentFilter, 'is_locked', 'payment_date')
    search_fields = ('receipt_number', 'student__name', 'notes')
    autocomplete_fields = ['student']
    date_hierarchy = 'payment_date'
    
    fieldsets = (
        ('Paiement', {
            'fields': ('student', 'amount', 'payment_date', 'month_covered')
        }),
        ('Détails', {
            'fields': ('status', 'payment_method', 'notes')
        }),
        ('Système', {
            'fields': ('receipt_number', 'is_locked', 'created_by', 'created_at'),
            'classes': ('collapse',)
        }),
    )
    
    readonly_fields = ('receipt_number', 'created_at')
    
    def amount_display(self, obj):
        return format_html('<strong style="font-size: 15px; color: #28a745;">{} DH</strong>', obj.amount)
    amount_display.short_description = 'Montant'
    
    def status_badge(self, obj):
        colors = {
            'PAID': '#28a745',
            'PENDING': '#ffc107',
            'CANCELLED': '#dc3545'
        }
        return format_html(
            '<span style="background: {}; color: white; padding: 3px 10px; border-radius: 3px;">{}</span>',
            colors.get(obj.status, 'gray'),
            obj.get_status_display()
        )
    status_badge.short_description = 'Statut'
    
    def locked_status(self, obj):
        if obj.is_locked:
            return mark_safe('<span style="color: red; font-size: 16px;">🔒 Verrouillé</span>')
        return mark_safe('<span style="color: green;">🔓 Modifiable</span>')


@admin.register(SessionException)
class SessionExceptionAdmin(ModelAdmin):
    list_display = ('course_group', 'date', 'cancelled', 'override_room', 'override_start_time', 'override_end_time')
    list_filter = ('cancelled', 'course_group__teacher', 'course_group__schedules__room')
    search_fields = ('course_group__name',)
    autocomplete_fields = ('course_group', 'override_room')
    
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # Regenerate sessions affected by this exception
        from .utils import generate_sessions_from_coursegroups
        from datetime import timedelta
        start = obj.date - timedelta(days=1)
        end = obj.date + timedelta(days=1)
        generate_sessions_from_coursegroups(start, end, force=True, course=obj.course_group)
        messages.success(request, f"✅ Exception de session enregistrée et sessions synchronisées pour le {obj.date}.")

    def delete_model(self, request, obj):
        course_group = obj.course_group
        date_val = obj.date
        super().delete_model(request, obj)
        # Regenerate sessions affected by this exception deletion
        from .utils import generate_sessions_from_coursegroups
        from datetime import timedelta
        start = date_val - timedelta(days=1)
        end = date_val + timedelta(days=1)
        generate_sessions_from_coursegroups(start, end, force=True, course=course_group)

    def delete_queryset(self, request, queryset):
        # Collect affected course groups and dates before deletion
        affected_items = list(queryset.values('course_group', 'date'))
        super().delete_queryset(request, queryset)
        # Regenerate sessions for each affected item
        from .utils import generate_sessions_from_coursegroups
        from datetime import timedelta
        for item in affected_items:
            try:
                cg = CourseGroup.objects.get(pk=item['course_group'])
                start = item['date'] - timedelta(days=1)
                end = item['date'] + timedelta(days=1)
                generate_sessions_from_coursegroups(start, end, force=True, course=cg)
            except CourseGroup.DoesNotExist:
                pass


@admin.register(Attendance)
class AttendanceAdmin(ModelAdmin):
    list_display = ('date', 'student', 'course_group', 'presence_badge', 'notes_preview')
    list_filter = ('is_present', 'date', 'course_group')
    search_fields = ('student__name', 'course_group__name')
    autocomplete_fields = ['student', 'course_group']
    date_hierarchy = 'date'
    
    def presence_badge(self, obj):
        if obj.is_present:
            return mark_safe('<span style="color: green; font-size: 18px; font-weight: bold;">✓ Présent</span>')
        return mark_safe('<span style="color: red; font-size: 18px; font-weight: bold;">✗ Absent</span>')
    presence_badge.short_description = 'Présence'
    
    def notes_preview(self, obj):
        if obj.notes:
            return obj.notes[:50] + ('...' if len(obj.notes) > 50 else '')
        return '-'
    notes_preview.short_description = 'Notes'


@admin.register(Session)
class SessionAdmin(ModelAdmin):
    list_display = ('date', 'group', 'room', 'get_teacher', 'start_time', 'end_time', 'status')
    list_filter = ('status', 'date', 'room', 'group__teacher')
    search_fields = ('group__name', 'group__teacher__name', 'room__name')
    autocomplete_fields = ['group']

    def get_teacher(self, obj):
        return obj.group.teacher.name if obj.group and obj.group.teacher else '-'
    get_teacher.short_description = 'Professeur'

    def save_model(self, request, obj, form, change):
        try:
            obj.is_manually_edited = True
            obj.full_clean()
            super().save_model(request, obj, form, change)
            messages.success(request, f"✅ Session pour {obj.group.name} enregistrée ({obj.date})")
        except ValidationError as e:
            messages.error(request, f"⚠️ Impossible d'enregistrer la session: {e.messages[0] if hasattr(e, 'messages') else e}")
            return


# ==================== CUSTOMISATION DU SITE ADMIN ====================

admin.site.site_header = "🎓 Centre Tonaroz - Gestion"
admin.site.site_title = "Admin Centre Tonaroz"
admin.site.index_title = "Tableau de Bord"
