Windows PowerShell
Copyright (C) Microsoft Corporation. All rights reserved.

Install the latest PowerShell for new features and improvements! https://aka.ms/PSWindows

PS C:\Users\manel> cd "C:\Users\manel\OneDrive\Documents\AI_Grading_Prototype\AI-Grading-Prototype"
PS C:\Users\manel\OneDrive\Documents\AI_Grading_Prototype\AI-Grading-Prototype> .\.venv\Scripts\Activate.ps1
(.venv) PS C:\Users\manel\OneDrive\Documents\AI_Grading_Prototype\AI-Grading-Prototype> python worker.py --host localhost
2026-07-08 17:00:21 [INFO] Pika version 1.4.1 connecting to ('::1', 5672, 0, 0)
2026-07-08 17:00:21 [INFO] Socket connected: <socket.socket fd=1320, family=23, type=1, proto=6, laddr=('::1', 57030, 0, 0), raddr=('::1', 5672, 0,0)>
2026-07-08 17:00:21 [INFO] Streaming transport linked up: (<pika.adapters.utils.io_services_utils._AsyncPlaintextTransport object at 0x0000020EF0483620>, _StreamingProtocolShim: <SelectConnection PROTOCOL transport=<pika.adapters.utils.io_services_utils._AsyncPlaintextTransport object at 0x0000020EF0483620> params=<ConnectionParameters host=localhost port=5672 virtual_host=/ ssl=False>>).
2026-07-08 17:00:21 [INFO] AMQPConnector - reporting success: <SelectConnection OPEN transport=<pika.adapters.utils.io_services_utils._AsyncPlaintextTransport object at 0x0000020EF0483620> params=<ConnectionParameters host=localhost port=5672 virtual_host=/ ssl=False>>
2026-07-08 17:00:21 [INFO] AMQPConnectionWorkflow - reporting success: <SelectConnection OPEN transport=<pika.adapters.utils.io_services_utils._AsyncPlaintextTransport object at 0x0000020EF0483620> params=<ConnectionParameters host=localhost port=5672 virtual_host=/ ssl=False>>
2026-07-08 17:00:21 [INFO] Connection workflow succeeded: <SelectConnection OPEN transport=<pika.adapters.utils.io_services_utils._AsyncPlaintextTransport object at 0x0000020EF0483620> params=<ConnectionParameters host=localhost port=5672 virtual_host=/ ssl=False>>
2026-07-08 17:00:21 [INFO] Created channel=1
2026-07-08 17:00:21 [INFO] === AI Grading Worker Started ===
2026-07-08 17:00:21 [INFO] Model   : gemini-2.5-flash
2026-07-08 17:00:21 [INFO] Output  : output.json
2026-07-08 17:00:21 [INFO] Log     : grading_log.json
2026-07-08 17:00:21 [INFO] Host    : localhost
2026-07-08 17:00:21 [INFO] Waiting for grading jobs... (Ctrl+C to stop)
2026-07-08 17:00:24 [INFO] [->] Received grading job for learner: u12345678
2026-07-08 17:00:24 [INFO] AFC is enabled with max remote calls: 10.
2026-07-08 17:00:29 [INFO] HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent "HTTP/1.1 200 OK"
2026-07-08 17:00:29 [INFO] [LOG:success] u12345678 -> 2.0/5.0 (Partial Pass)
2026-07-08 17:00:29 [INFO] [OK] u12345678 -> 2.0/5.0 (40.0%) [Partial Pass]
2026-07-08 17:00:30 [INFO] Essay question graded: attempt=12 slot=11 result={'attemptid': 12, 'slot': 11, 'mark': 2, 'maxmark': 5, 'sumgrades': 0, 'quizgrade': 30, 'status': 'partiallycorrect'}
2026-07-08 17:00:30 [INFO] [QUIZ] Accumulated 1/2 essay grades for attempt=12 userid=4
2026-07-08 17:00:30 [INFO] [->] Received grading job for learner: u12345678
2026-07-08 17:00:30 [INFO] AFC is enabled with max remote calls: 10.
2026-07-08 17:00:40 [INFO] HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent "HTTP/1.1 200 OK"
2026-07-08 17:00:40 [INFO] [LOG:success] u12345678 -> 8.0/10.0 (Good)
2026-07-08 17:00:40 [INFO] [OK] u12345678 -> 8.0/10.0 (80.0%) [Good]
2026-07-08 17:00:40 [INFO] Essay question graded: attempt=12 slot=12 result={'attemptid': 12, 'slot': 12, 'mark': 8, 'maxmark': 10, 'sumgrades': 14, 'quizgrade': 30, 'status': 'partiallycorrect'}
2026-07-08 17:00:40 [INFO] [QUIZ] Accumulated 2/2 essay grades for attempt=12 userid=4
2026-07-08 17:00:40 [INFO] [QUIZ] Essay total: 10.0 + Objective score: 4.0 = Grand total: 14.0
2026-07-08 17:00:40 [INFO] [MOODLE] Quiz grade pushed back for u12345678 (userid=4, quiz=1, final=14.0)
2026-07-08 17:00:40 [INFO] [->] Received grading job for learner: u98765432
2026-07-08 17:00:40 [INFO] AFC is enabled with max remote calls: 10.
2026-07-08 17:00:46 [INFO] HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent "HTTP/1.1 200 OK"
2026-07-08 17:00:47 [INFO] [LOG:success] u98765432 -> 5.0/5.0 (Excellent)
2026-07-08 17:00:47 [INFO] [OK] u98765432 -> 5.0/5.0 (100.0%) [Excellent]
2026-07-08 17:00:47 [INFO] Essay question graded: attempt=13 slot=11 result={'attemptid': 13, 'slot': 11, 'mark': 5, 'maxmark': 5, 'sumgrades': 0, 'quizgrade': 30, 'status': 'correct'}
2026-07-08 17:00:47 [INFO] [QUIZ] Accumulated 1/2 essay grades for attempt=13 userid=5
2026-07-08 17:00:47 [INFO] [->] Received grading job for learner: u98765432
2026-07-08 17:00:47 [INFO] AFC is enabled with max remote calls: 10.
2026-07-08 17:00:54 [INFO] HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent "HTTP/1.1 200 OK"
2026-07-08 17:00:54 [INFO] [LOG:success] u98765432 -> 10.0/10.0 (Excellent)
2026-07-08 17:00:54 [INFO] [OK] u98765432 -> 10.0/10.0 (100.0%) [Excellent]
2026-07-08 17:00:54 [INFO] Essay question graded: attempt=13 slot=12 result={'attemptid': 13, 'slot': 12, 'mark': 10, 'maxmark': 10, 'sumgrades': 21, 'quizgrade': 30, 'status': 'correct'}
2026-07-08 17:00:54 [INFO] [QUIZ] Accumulated 2/2 essay grades for attempt=13 userid=5
2026-07-08 17:00:54 [INFO] [QUIZ] Essay total: 15.0 + Objective score: 6.0 = Grand total: 21.0
2026-07-08 17:00:54 [INFO] [MOODLE] Quiz grade pushed back for u98765432 (userid=5, quiz=1, final=21.0)
2026-07-08 17:00:54 [INFO] [->] Received grading job for learner: u24688642
2026-07-08 17:00:54 [INFO] AFC is enabled with max remote calls: 10.
2026-07-08 17:01:01 [INFO] HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent "HTTP/1.1 200 OK"
2026-07-08 17:01:01 [INFO] [LOG:success] u24688642 -> 5.0/5.0 (Excellent)
2026-07-08 17:01:01 [INFO] [OK] u24688642 -> 5.0/5.0 (100.0%) [Excellent]
2026-07-08 17:01:01 [INFO] Essay question graded: attempt=10 slot=11 result={'attemptid': 10, 'slot': 11, 'mark': 5, 'maxmark': 5, 'sumgrades': 0, 'quizgrade': 30, 'status': 'correct'}
2026-07-08 17:01:01 [INFO] [QUIZ] Accumulated 1/2 essay grades for attempt=10 userid=6
2026-07-08 17:01:01 [INFO] [->] Received grading job for learner: u24688642
2026-07-08 17:01:01 [INFO] AFC is enabled with max remote calls: 10.
2026-07-08 17:01:10 [INFO] HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent "HTTP/1.1 200 OK"
2026-07-08 17:01:10 [INFO] [LOG:success] u24688642 -> 10.0/10.0 (Excellent)
2026-07-08 17:01:10 [INFO] [OK] u24688642 -> 10.0/10.0 (100.0%) [Excellent]
2026-07-08 17:01:10 [INFO] Essay question graded: attempt=10 slot=12 result={'attemptid': 10, 'slot': 12, 'mark': 10, 'maxmark': 10, 'sumgrades': 24, 'quizgrade': 30, 'status': 'correct'}
2026-07-08 17:01:10 [INFO] [QUIZ] Accumulated 2/2 essay grades for attempt=10 userid=6
2026-07-08 17:01:10 [INFO] [QUIZ] Essay total: 15.0 + Objective score: 9.0 = Grand total: 24.0
2026-07-08 17:01:10 [INFO] [MOODLE] Quiz grade pushed back for u24688642 (userid=6, quiz=1, final=24.0)
2026-07-08 17:06:12 [INFO] [->] Received grading job for learner: u78191898
2026-07-08 17:06:12 [INFO] AFC is enabled with max remote calls: 10.
2026-07-08 17:06:18 [INFO] HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent "HTTP/1.1 200 OK"
2026-07-08 17:06:18 [INFO] [LOG:success] u78191898 -> 5.0/5.0 (Excellent)
2026-07-08 17:06:18 [INFO] [OK] u78191898 -> 5.0/5.0 (100.0%) [Excellent]
2026-07-08 17:06:18 [INFO] Essay question graded: attempt=11 slot=11 result={'attemptid': 11, 'slot': 11, 'mark': 5, 'maxmark': 5, 'sumgrades': 0, 'quizgrade': 30, 'status': 'correct'}
2026-07-08 17:06:18 [INFO] [QUIZ] Accumulated 1/2 essay grades for attempt=11 userid=8
2026-07-08 17:06:18 [INFO] [->] Received grading job for learner: u78191898
2026-07-08 17:06:18 [INFO] AFC is enabled with max remote calls: 10.
2026-07-08 17:06:25 [INFO] HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent "HTTP/1.1 200 OK"
2026-07-08 17:06:25 [INFO] [LOG:success] u78191898 -> 10.0/10.0 (Excellent)
2026-07-08 17:06:25 [INFO] [OK] u78191898 -> 10.0/10.0 (100.0%) [Excellent]
2026-07-08 17:06:26 [INFO] Essay question graded: attempt=11 slot=12 result={'attemptid': 11, 'slot': 12, 'mark': 10, 'maxmark': 10, 'sumgrades': 29, 'quizgrade': 30, 'status': 'correct'}
2026-07-08 17:06:26 [INFO] [QUIZ] Accumulated 2/2 essay grades for attempt=11 userid=8
2026-07-08 17:06:26 [INFO] [QUIZ] Essay total: 15.0 + Objective score: 14.0 = Grand total: 29.0
2026-07-08 17:06:26 [INFO] [MOODLE] Quiz grade pushed back for u78191898 (userid=8, quiz=1, final=29.0)
