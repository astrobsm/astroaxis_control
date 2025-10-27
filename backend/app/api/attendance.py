from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import Optional, List
from uuid import UUID
from datetime import datetime, date, time, timedelta

from app.db import get_session
from app.models import Attendance, Staff
from app.schemas import AttendanceSchema, AttendanceCreate, PaginatedResponse, QuickAttendanceRequest, QuickAttendanceResponse
from sqlalchemy import func, and_, or_

router = APIRouter(prefix='/api/attendance')

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
        now = datetime.utcnow()
        attendance = Attendance(staff_id=att.staff_id, clock_in=now, notes=att.notes)
        session.add(attendance)
        await session.commit()
        await session.refresh(attendance)
        return attendance
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error clocking in: {str(e)}")

@router.post('/clock-out', response_model=AttendanceSchema)
async def clock_out(staff_id: UUID = Query(...), session: AsyncSession = Depends(get_session)):
    """Clock out the latest open attendance for a staff member and compute hours worked"""
    try:
        # find latest open attendance
        result = await session.execute(
            select(Attendance).where(Attendance.staff_id == staff_id).where(Attendance.clock_out == None).order_by(Attendance.clock_in.desc())
        )
        attendance = result.scalars().first()
        if not attendance:
            raise HTTPException(status_code=404, detail="Open attendance record not found for staff")

        now = datetime.utcnow()
        attendance.clock_out = now
        # compute hours worked
        delta = now - attendance.clock_in
        hours = round(delta.total_seconds() / 3600.0, 2)
        attendance.hours_worked = hours
        attendance.status = 'completed'
        await session.commit()
        await session.refresh(attendance)
        return attendance
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error clocking out: {str(e)}")

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

    result = await session.execute(query.offset(skip).limit(limit).order_by(Attendance.clock_in.desc()))
    items = result.scalars().all()

    return PaginatedResponse(items=items, total=total, skip=skip, limit=limit, pages=(total + limit - 1) // limit)


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

        now = datetime.utcnow()
        
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
                now = datetime.utcnow()
                duration = now - open_attendance.clock_in
                hours = duration.total_seconds() / 3600
                
                status_list.append({
                    'staff_id': str(staff.id),
                    'employee_id': staff.employee_id,
                    'staff_name': f"{staff.first_name} {staff.last_name}",
                    'position': staff.position,
                    'status': 'clocked_in',
                    'clock_in_time': open_attendance.clock_in.strftime('%Y-%m-%d %H:%M:%S'),
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
                    status_list.append({
                        'staff_id': str(staff.id),
                        'employee_id': staff.employee_id,
                        'staff_name': f"{staff.first_name} {staff.last_name}",
                        'position': staff.position,
                        'status': 'clocked_out',
                        'clock_in_time': completed.clock_in.strftime('%Y-%m-%d %H:%M:%S'),
                        'clock_out_time': completed.clock_out.strftime('%Y-%m-%d %H:%M:%S'),
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
            clock_in_time = attendance.clock_in.time()
            attendance_date = attendance.clock_in.date()
            
            # Calculate punctuality
            expected_datetime = datetime.combine(attendance_date, STANDARD_START_TIME)
            actual_datetime = attendance.clock_in
            
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
            
            detailed_log.append({
                'attendance_id': str(attendance.id),
                'staff_id': str(staff.id),
                'employee_id': staff.employee_id,
                'staff_name': f"{staff.first_name} {staff.last_name}",
                'position': staff.position,
                'date': attendance_date.strftime('%Y-%m-%d'),
                'clock_in': attendance.clock_in.strftime('%H:%M:%S'),
                'clock_out': attendance.clock_out.strftime('%H:%M:%S') if attendance.clock_out else None,
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
                clock_in_time = attendance.clock_in
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
