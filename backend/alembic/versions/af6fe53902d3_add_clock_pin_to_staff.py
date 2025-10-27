"""add_clock_pin_to_staff

Revision ID: af6fe53902d3
Revises: f3209ab69b1f
Create Date: 2025-10-25 09:20:38.374314

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'af6fe53902d3'
down_revision: Union[str, None] = 'f3209ab69b1f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add clock_pin column as nullable first
    op.add_column('staff', sa.Column('clock_pin', sa.String(length=4), nullable=True))
    
    # Generate unique PINs for existing staff
    import random
    import string
    
    # Get connection to update existing records
    connection = op.get_bind()
    
    # Fetch all existing staff without PINs
    result = connection.execute(sa.text("SELECT id FROM staff WHERE clock_pin IS NULL"))
    staff_ids = [row[0] for row in result]
    
    # Generate unique PINs for existing staff
    used_pins = set()
    for staff_id in staff_ids:
        # Generate unique PIN
        pin = None
        attempts = 0
        while pin is None and attempts < 1000:
            candidate = ''.join(random.choices(string.digits, k=4))
            if candidate not in used_pins:
                pin = candidate
                used_pins.add(pin)
            attempts += 1
        
        if pin is None:
            raise Exception("Could not generate unique PIN")
            
        # Update the staff record
        connection.execute(
            sa.text("UPDATE staff SET clock_pin = :pin WHERE id = :staff_id"),
            {"pin": pin, "staff_id": staff_id}
        )
    
    # Now make the column NOT NULL
    op.alter_column('staff', 'clock_pin', nullable=False)
    op.create_index(op.f('ix_staff_clock_pin'), 'staff', ['clock_pin'], unique=True)


def downgrade() -> None:
    # Remove clock_pin column from staff table
    op.drop_index(op.f('ix_staff_clock_pin'), table_name='staff')
    op.drop_column('staff', 'clock_pin')
