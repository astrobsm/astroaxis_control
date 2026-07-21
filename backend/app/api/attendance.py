from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import Optional, List
from uuid import UUID
from datetime import datetime, date, time, timedelta, timezone
import asyncio
import logging

from app.db import get_session, AsyncSessionLocal
from app.models import Attendance, Staff
from app.schemas import AttendanceSchema, AttendanceCreate, PaginatedResponse, QuickAttendanceRequest, QuickAttendanceResponse
from sqlalchemy import func, and_, or_, text
from app.services.geocoding import reverse_geocode

router = APIRouter(prefix='/api/attendance')

# ---- Auto clock-out scheduler config ----
# Lagos is UTC+1 year-round (no DST). Auto clock-out triggers at 17:10 local.
LAGOS_TZ = timezone(timedelta(hours=1))
AUTO_CLOCKOUT_HOUR = 17
AUTO_CLOCKOUT_MINUTE = 10
_AUTO_CLOCKOUT_LOG = logging.getLogger("attendance.auto_clockout")

@router.post('/clock-in', response_model=AttendanceSchema)
async def clock_in(att: AttendanceCreate, session: AsyncSession = Depends(get_session)):
    """Clock in a staff member - creates an open attendance record with current timestamp"""
    try:
        # verify staff exists
        staff_result = await session.execute(select(Staff).where(Staff.id == att.staff_id))
        staff = staff_result.scalars().first()
        if not staff:
            raise HTTPException(status_code=404, detail="Staff not found")

        # create attendance
        now = datetime.now(timezone.utc)
        attendance = Attendance(staff_id=att.staff_id, clock_in=now, notes=att.notes)
        session.add(attendance)
        await session.commit()
        await session.refresh(attendance)
        # Best-effort geo persist (separate UPDATE so failure can't block clock-in)
        if att.latitude is not None and att.longitude is not None:
            try:
                address = await reverse_geocode(att.latitude, att.longitude)
                await session.execute(text(
                    "UPDATE attendance SET clock_in_lat=:lat, clock_in_lng=:lng, "
                    "clock_in_accuracy=:acc, clock_in_address=:addr WHERE id=:id"
                ), {
                    'lat': att.latitude, 'lng': att.longitude,
                    'acc': att.accuracy, 'addr': address,
                    'id': attendance.id,
                })
                await session.commit()
            except Exception:
                await session.rollback()
        return attendance
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error clocking in: {str(e)}")

@router.post('/clock-out', response_model=AttendanceSchema)
async def clock_out(
    staff_id: UUID = Query(...),
    latitude: Optional[float] = Query(None),
    longitude: Optional[float] = Query(None),
    accuracy: Optional[float] = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """Clock out the latest open attendance for a staff member and compute hours worked"""
    try:
        # find latest open attendance
        result = await session.execute(
            select(Attendance).where(Attendance.staff_id == staff_id).where(Attendance.clock_out == None).order_by(Attendance.clock_in.desc())
        )
        attendance = result.scalars().first()
        if not attendance:
            raise HTTPException(status_code=404, detail="Open attendance record not found for staff")

        now = datetime.now(timezone.utc)
        attendance.clock_out = now
        # compute hours worked
        delta = now - attendance.clock_in
        hours = round(delta.total_seconds() / 3600.0, 2)
        attendance.hours_worked = hours
        attendance.status = 'completed'
        await session.commit()
        await session.refresh(attendance)
        # Best-effort geo persist
        if latitude is not None and longitude is not None:
            try:
                address = await reverse_geocode(latitude, longitude)
                await session.execute(text(
                    "UPDATE attendance SET clock_out_lat=:lat, clock_out_lng=:lng, "
                    "clock_out_accuracy=:acc, clock_out_address=:addr WHERE id=:id"
                ), {
                    'lat': latitude, 'lng': longitude,
                    'acc': accuracy, 'addr': address,
                    'id': attendance.id,
                })
                await session.commit()
            except Exception:
                await session.rollback()
        return attendance
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error clocking out: {str(e)}")

@router.post('/bulk-clock-out')
async def bulk_clock_out(
    payload: Optional[dict] = None,
    session: AsyncSession = Depends(get_session),
):
    """Bulk clock-out: closes ALL currently-open attendance records (or only the
    given staff_ids if provided in the request body as {"staff_ids": [...]}).

    Used by admins / production supervisor to close out forgotten clock-ins at
    the end of a shift.
    """
    try:
        staff_ids: List[UUID] = []
        if payload and isinstance(payload, dict):
            raw_ids = payload.get('staff_ids') or []
            for sid in raw_ids:
                try:
                    staff_ids.append(UUID(str(sid)))
                except (ValueError, TypeError):
                    continue
        result = await _close_open_attendance(
            session,
            staff_ids=staff_ids or None,
            marker='BULK CLOCK-OUT',
        )
        return {'success': True, **result}
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Bulk clock-out failed: {str(e)}")


async def _close_open_attendance(
    session: AsyncSession,
    staff_ids: Optional[List[UUID]] = None,
    marker: str = 'AUTO CLOCK-OUT',
) -> dict:
    """Close all open Attendance rows (clock_out IS NULL).

    Optionally restrict to specific staff_ids. Computes hours_worked and stamps
    a marker into notes. Commits the session before returning.
    """
    query = select(Attendance).where(Attendance.clock_out == None)  # noqa: E711
    if staff_ids:
        query = query.where(Attendance.staff_id.in_(staff_ids))

    res = await session.execute(query)
    open_records = res.scalars().all()

    now = datetime.now(timezone.utc)
    closed = 0
    details = []
    for att in open_records:
        try:
            att.clock_out = now
            delta = now - att.clock_in
            hours = round(delta.total_seconds() / 3600.0, 2)
            att.hours_worked = hours
            att.status = 'completed'
            att.notes = (
                f"{att.notes or ''}\n[{marker} at {now.strftime('%Y-%m-%d %H:%M UTC')}]"
            ).strip()
            closed += 1
            details.append({
                'attendance_id': str(att.id),
                'staff_id': str(att.staff_id),
                'hours_worked': hours,
            })
        except Exception as inner:
            details.append({
                'attendance_id': str(att.id),
                'staff_id': str(att.staff_id),
                'error': str(inner),
            })
    await session.commit()
    return {
        'closed_count': closed,
        'total_open_found': len(open_records),
        'closed_at': now.isoformat(),
        'details': details,
    }


async def auto_clockout_scheduler():
    """Background loop: every 60s, check Lagos local time. Once we're past
    17:10 local on a given date, close any still-open attendance records.

    The operation is naturally idempotent — once records are closed, the next
    pass finds nothing to do until new clock-ins occur.
    """
    _AUTO_CLOCKOUT_LOG.info(
        "Auto clock-out scheduler started (target %02d:%02d Africa/Lagos)",
        AUTO_CLOCKOUT_HOUR, AUTO_CLOCKOUT_MINUTE,
    )
    last_run_date = None
    while True:
        try:
            await asyncio.sleep(60)
            local_now = datetime.now(LAGOS_TZ)
            trigger = local_now.replace(
                hour=AUTO_CLOCKOUT_HOUR,
                minute=AUTO_CLOCKOUT_MINUTE,
                second=0,
                microsecond=0,
            )
            if local_now < trigger:
                continue
            if last_run_date == local_now.date():
                # Already swept today; still run again to catch any post-17:10
                # late clock-ins, but limit work by only touching open rows.
                pass
            async with AsyncSessionLocal() as session:
                result = await _close_open_attendance(
                    session, marker=f'AUTO CLOCK-OUT 17:10'
                )
            if result.get('closed_count'):
                _AUTO_CLOCKOUT_LOG.info(
                    "Auto clock-out closed %s open record(s) at %s",
                    result['closed_count'], result['closed_at'],
                )
            last_run_date = local_now.date()
        except asyncio.CancelledError:
            _AUTO_CLOCKOUT_LOG.info("Auto clock-out scheduler cancelled")
            raise
        except Exception:
            _AUTO_CLOCKOUT_LOG.exception("Auto clock-out iteration failed")
            await asyncio.sleep(30)



@router.get('/', response_model=PaginatedResponse[AttendanceSchema])
async def list_attendance(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    staff_id: Optional[UUID] = Query(None),
    start: Optional[date] = Query(None),
    end: Optional[date] = Query(None),
    session: AsyncSession = Depends(get_session)
):
    """List attendance records with filters"""
    query = select(Attendance)
    if staff_id:
        query = query.where(Attendance.staff_id == staff_id)
    if start:
        query = query.where(func.date(Attendance.clock_in) >= start)
    if end:
        query = query.where(func.date(Attendance.clock_in) <= end)

    count_query = select(func.count(Attendance.id))
    if staff_id:
        count_query = count_query.where(Attendance.staff_id == staff_id)
    if start:
        count_query = count_query.where(func.date(Attendance.clock_in) >= start)
    if end:
        count_query = count_query.where(func.date(Attendance.clock_in) <= end)

    count_result = await session.execute(count_query)
    total = count_result.scalar_one()

    # Calculate offset from page
    offset = skip
    size = limit
    page = (skip // limit) + 1 if limit > 0 else 1
    
    result = await session.execute(query.offset(offset).limit(size).order_by(Attendance.clock_in.desc()))
    items = result.scalars().all()

    return PaginatedResponse(items=items, total=total, page=page, size=size, pages=(total + size - 1) // size if size > 0 else 1)


@router.post('/quick-attendance', response_model=QuickAttendanceResponse)
async def quick_attendance(request: QuickAttendanceRequest, session: AsyncSession = Depends(get_session)):
    """PIN-based clock in/out for staff without requiring login"""
    try:
        # Find staff by PIN
        staff_result = await session.execute(select(Staff).where(Staff.clock_pin == request.pin).where(Staff.is_active == True))
        staff = staff_result.scalars().first()
        
        if not staff:
            return QuickAttendanceResponse(
                success=False,
                message="Invalid PIN. Please check your PIN and try again."
            )

        now = datetime.now(timezone.utc)
        
        if request.action == "clock_in":
            # Check if staff already has an open attendance record
            existing_result = await session.execute(
                select(Attendance).where(Attendance.staff_id == staff.id)
                .where(Attendance.clock_out == None)
                .order_by(Attendance.clock_in.desc())
            )
            existing_attendance = existing_result.scalars().first()
            
            if existing_attendance:
                return QuickAttendanceResponse(
                    success=False,
                    message=f"You are already clocked in since {existing_attendance.clock_in.strftime('%H:%M on %Y-%m-%d')}. Please clock out first."
                )
            
            # Create new attendance record
            attendance = Attendance(
                staff_id=staff.id,
                clock_in=now,
                notes=request.notes
            )
            session.add(attendance)
            await session.commit()
            # Best-effort geo persist
            if request.latitude is not None and request.longitude is not None:
                try:
                    address = await reverse_geocode(request.latitude, request.longitude)
                    await session.execute(text(
                        "UPDATE attendance SET clock_in_lat=:lat, clock_in_lng=:lng, "
                        "clock_in_accuracy=:acc, clock_in_address=:addr WHERE id=:id"
                    ), {
                        'lat': request.latitude, 'lng': request.longitude,
                        'acc': request.accuracy, 'addr': address,
                        'id': attendance.id,
                    })
                    await session.commit()
                except Exception:
                    await session.rollback()
            
            return QuickAttendanceResponse(
                success=True,
                message=f"Successfully clocked in at {now.strftime('%H:%M')}",
                staff_name=f"{staff.first_name} {staff.last_name}",
                action="clock_in",
                timestamp=now
            )
            
        elif request.action == "clock_out":
            # Find latest open attendance record
            attendance_result = await session.execute(
                select(Attendance).where(Attendance.staff_id == staff.id)
                .where(Attendance.clock_out == None)
                .order_by(Attendance.clock_in.desc())
            )
            attendance = attendance_result.scalars().first()
            
            if not attendance:
                return QuickAttendanceResponse(
                    success=False,
                    message="No open clock-in record found. Please clock in first."
                )
            
            # Update attendance record with clock out time
            attendance.clock_out = now
            time_diff = now - attendance.clock_in
            hours_worked = round(time_diff.total_seconds() / 3600, 2)
            attendance.hours_worked = hours_worked
            attendance.status = 'completed'
            
            if request.notes:
                attendance.notes = f"{attendance.notes or ''}\nClock-out: {request.notes}".strip()
            
            await session.commit()
            # Best-effort geo persist (clock-out)
            if request.latitude is not None and request.longitude is not None:
                try:
                    address = await reverse_geocode(request.latitude, request.longitude)
                    await session.execute(text(
                        "UPDATE attendance SET clock_out_lat=:lat, clock_out_lng=:lng, "
                        "clock_out_accuracy=:acc, clock_out_address=:addr WHERE id=:id"
                    ), {
                        'lat': request.latitude, 'lng': request.longitude,
                        'acc': request.accuracy, 'addr': address,
                        'id': attendance.id,
                    })
                    await session.commit()
                except Exception:
                    await session.rollback()
            
            return QuickAttendanceResponse(
                success=True,
                message=f"Successfully clocked out at {now.strftime('%H:%M')}",
                staff_name=f"{staff.first_name} {staff.last_name}",
                action="clock_out",
                timestamp=now,
                hours_worked=hours_worked
            )
        
        else:
            return QuickAttendanceResponse(
                success=False,
                message="Invalid action. Use 'clock_in' or 'clock_out'."
            )
            
    except Exception as e:
        await session.rollback()
        return QuickAttendanceResponse(
            success=False,
            message=f"System error: {str(e)}"
        )

@router.get('/status', response_model=List[dict])
async def get_attendance_status(session: AsyncSession = Depends(get_session)):
    """Get current attendance status for all staff - who's clocked in, who's clocked out"""
    try:
        # Get all active staff
        staff_result = await session.execute(
            select(Staff).where(Staff.is_active == True).order_by(Staff.first_name, Staff.last_name)
        )
        all_staff = staff_result.scalars().all()
        
        status_list = []
        for staff in all_staff:
            # Check if staff has an open attendance record today
            today_start = datetime.combine(date.today(), time.min)
            open_attendance_result = await session.execute(
                select(Attendance)
                .where(Attendance.staff_id == staff.id)
                .where(Attendance.clock_out == None)
                .where(Attendance.clock_in >= today_start)
                .order_by(Attendance.clock_in.desc())
            )
            open_attendance = open_attendance_result.scalars().first()
            
            if open_attendance:
                # Calculate how long they've been clocked in
                now = datetime.now(timezone.utc)
                # Make clock_in timezone-naive if it's timezone-aware
                clock_in_naive = open_attendance.clock_in.replace(tzinfo=None) if open_attendance.clock_in.tzinfo else open_attendance.clock_in
                duration = now - clock_in_naive
                hours = duration.total_seconds() / 3600
                
                clock_in_str = clock_in_naive.strftime('%Y-%m-%d %H:%M:%S')
                status_list.append({
                    'staff_id': str(staff.id),
                    'employee_id': staff.employee_id,
                    'staff_name': f"{staff.first_name} {staff.last_name}",
                    'position': staff.position,
                    'status': 'clocked_in',
                    'clock_in_time': clock_in_str,
                    'hours_so_far': round(hours, 2),
                    'notes': open_attendance.notes
                })
            else:
                # Check if they clocked out today
                today_end = datetime.combine(date.today(), time.max)
                completed_result = await session.execute(
                    select(Attendance)
                    .where(Attendance.staff_id == staff.id)
                    .where(Attendance.clock_out != None)
                    .where(Attendance.clock_in >= today_start)
                    .where(Attendance.clock_in <= today_end)
                    .order_by(Attendance.clock_out.desc())
                )
                completed = completed_result.scalars().first()
                
                if completed:
                    # Make datetimes timezone-naive if they're timezone-aware
                    clock_in_naive = completed.clock_in.replace(tzinfo=None) if completed.clock_in.tzinfo else completed.clock_in
                    clock_out_naive = completed.clock_out.replace(tzinfo=None) if completed.clock_out.tzinfo else completed.clock_out
                    status_list.append({
                        'staff_id': str(staff.id),
                        'employee_id': staff.employee_id,
                        'staff_name': f"{staff.first_name} {staff.last_name}",
                        'position': staff.position,
                        'status': 'clocked_out',
                        'clock_in_time': clock_in_naive.strftime('%Y-%m-%d %H:%M:%S'),
                        'clock_out_time': clock_out_naive.strftime('%Y-%m-%d %H:%M:%S'),
                        'hours_worked': completed.hours_worked,
                        'notes': completed.notes
                    })
                else:
                    status_list.append({
                        'staff_id': str(staff.id),
                        'employee_id': staff.employee_id,
                        'staff_name': f"{staff.first_name} {staff.last_name}",
                        'position': staff.position,
                        'status': 'not_clocked_in',
                        'notes': None
                    })
        
        return status_list
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching attendance status: {str(e)}")


@router.get('/detailed-log', response_model=List[dict])
async def get_detailed_attendance_log(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    session: AsyncSession = Depends(get_session)
):
    """Get detailed attendance log with punctuality analysis"""
    try:
        # Default to last 30 days if no dates provided
        if not end_date:
            end_date = date.today()
        if not start_date:
            start_date = end_date - timedelta(days=30)
        
        # Standard work time (9:00 AM)
        STANDARD_START_TIME = time(9, 0)
        
        # Get all attendance records in date range
        query = select(Attendance, Staff).join(Staff, Attendance.staff_id == Staff.id)
        query = query.where(func.date(Attendance.clock_in) >= start_date)
        query = query.where(func.date(Attendance.clock_in) <= end_date)
        query = query.order_by(Attendance.clock_in.desc())
        
        result = await session.execute(query)
        records = result.all()
        
        detailed_log = []
        for attendance, staff in records:
            # Make datetime timezone-naive if it's timezone-aware
            clock_in_naive = attendance.clock_in.replace(tzinfo=None) if attendance.clock_in.tzinfo else attendance.clock_in
            clock_in_time = clock_in_naive.time()
            attendance_date = clock_in_naive.date()
            
            # Calculate punctuality
            expected_datetime = datetime.combine(attendance_date, STANDARD_START_TIME)
            actual_datetime = clock_in_naive
            
            time_diff = (actual_datetime - expected_datetime).total_seconds() / 60  # minutes
            
            if time_diff <= 0:
                punctuality_status = 'early'
                punctuality_minutes = abs(int(time_diff))
            elif time_diff <= 15:
                punctuality_status = 'on_time'
                punctuality_minutes = int(time_diff)
            elif time_diff <= 30:
                punctuality_status = 'slightly_late'
                punctuality_minutes = int(time_diff)
            else:
                punctuality_status = 'late'
                punctuality_minutes = int(time_diff)
            
            # Handle clock_out timezone
            clock_out_str = None
            if attendance.clock_out:
                clock_out_naive = attendance.clock_out.replace(tzinfo=None) if attendance.clock_out.tzinfo else attendance.clock_out
                clock_out_str = clock_out_naive.strftime('%H:%M:%S')
            
            detailed_log.append({
                'attendance_id': str(attendance.id),
                'staff_id': str(staff.id),
                'employee_id': staff.employee_id,
                'staff_name': f"{staff.first_name} {staff.last_name}",
                'position': staff.position,
                'date': attendance_date.strftime('%Y-%m-%d'),
                'clock_in': clock_in_naive.strftime('%H:%M:%S'),
                'clock_out': clock_out_str,
                'hours_worked': attendance.hours_worked,
                'punctuality_status': punctuality_status,
                'punctuality_minutes': punctuality_minutes,
                'status': attendance.status,
                'notes': attendance.notes
            })
        
        return detailed_log
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching detailed log: {str(e)}")


@router.get('/best-performers', response_model=List[dict])
async def get_best_performing_staff(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    limit: int = Query(10, ge=1, le=50),
    session: AsyncSession = Depends(get_session)
):
    """Get best performing staff based on punctuality and attendance"""
    try:
        # Default to last 30 days if no dates provided
        if not end_date:
            end_date = date.today()
        if not start_date:
            start_date = end_date - timedelta(days=30)
        
        STANDARD_START_TIME = time(9, 0)
        
        # Get all active staff
        staff_result = await session.execute(select(Staff).where(Staff.is_active == True))
        all_staff = staff_result.scalars().all()
        
        performance_data = []
        
        for staff in all_staff:
            # Get attendance records for this staff in date range
            attendance_query = select(Attendance).where(Attendance.staff_id == staff.id)
            attendance_query = attendance_query.where(func.date(Attendance.clock_in) >= start_date)
            attendance_query = attendance_query.where(func.date(Attendance.clock_in) <= end_date)
            attendance_query = attendance_query.where(Attendance.clock_out != None)  # Only completed records
            
            attendance_result = await session.execute(attendance_query)
            attendance_records = attendance_result.scalars().all()
            
            if not attendance_records:
                continue
            
            total_days = len(attendance_records)
            total_hours = sum(a.hours_worked or 0 for a in attendance_records)
            
            # Calculate punctuality metrics
            early_count = 0
            on_time_count = 0
            late_count = 0
            total_late_minutes = 0
            
            for attendance in attendance_records:
                # Make datetime timezone-naive if it's timezone-aware
                clock_in_naive = attendance.clock_in.replace(tzinfo=None) if attendance.clock_in.tzinfo else attendance.clock_in
                clock_in_time = clock_in_naive
                attendance_date = clock_in_time.date()
                expected_datetime = datetime.combine(attendance_date, STANDARD_START_TIME)
                
                time_diff = (clock_in_time - expected_datetime).total_seconds() / 60
                
                if time_diff <= 0:
                    early_count += 1
                elif time_diff <= 15:
                    on_time_count += 1
                else:
                    late_count += 1
                    total_late_minutes += time_diff
            
            # Calculate punctuality score (0-100)
            punctuality_score = ((early_count + on_time_count) / total_days) * 100 if total_days > 0 else 0
            
            # Calculate average hours per day
            avg_hours_per_day = total_hours / total_days if total_days > 0 else 0
            
            # Calculate overall performance score
            # 70% punctuality, 30% attendance regularity
            attendance_score = (total_days / max((end_date - start_date).days, 1)) * 100
            performance_score = (punctuality_score * 0.7) + (min(attendance_score, 100) * 0.3)
            
            performance_data.append({
                'staff_id': str(staff.id),
                'employee_id': staff.employee_id,
                'staff_name': f"{staff.first_name} {staff.last_name}",
                'position': staff.position,
                'total_days_attended': total_days,
                'total_hours_worked': round(total_hours, 2),
                'avg_hours_per_day': round(avg_hours_per_day, 2),
                'early_arrivals': early_count,
                'on_time_arrivals': on_time_count,
                'late_arrivals': late_count,
                'avg_late_minutes': round(total_late_minutes / late_count, 2) if late_count > 0 else 0,
                'punctuality_score': round(punctuality_score, 2),
                'performance_score': round(performance_score, 2)
            })
        
        # Sort by performance score descending
        performance_data.sort(key=lambda x: x['performance_score'], reverse=True)
        
        return performance_data[:limit]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error calculating best performers: {str(e)}")

