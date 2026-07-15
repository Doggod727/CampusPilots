BEGIN;

INSERT INTO campus_service.electricity_accounts
    (room_id, campus_code, dormitory_area, building, room, balance,
     currency, source, is_simulated, source_updated_at)
SELECT
    '21000000-0000-4000-8000-000000000001'::uuid,
    c.code,
    '学生公寓东区',
    'A1',
    '301',
    23.50,
    'CNY',
    'mock',
    true,
    now()
FROM campus_service.campuses c
ORDER BY c.sort_order, c.code
LIMIT 1
ON CONFLICT (room_id) DO UPDATE SET
    balance = EXCLUDED.balance,
    source = 'mock',
    is_simulated = true,
    source_updated_at = EXCLUDED.source_updated_at;

-- Python seed_demo must bind the actual demo student UUID to the room:
-- INSERT INTO campus_service.electricity_account_members (room_id, user_id)
-- VALUES ('21000000-0000-4000-8000-000000000001', :demo_student_user_id)
-- ON CONFLICT DO NOTHING;

COMMIT;
