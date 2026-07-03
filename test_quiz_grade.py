import os, requests
from dotenv import load_dotenv
load_dotenv()

token = os.getenv("MOODLE_TOKEN")
base_url = os.getenv("MOODLE_BASE_URL", "http://localhost:8080")
endpoint = f"{base_url}/webservice/rest/server.php"

test_cases = [
    {
        "name": "Test 1 - basic",
        "params": {
            "wstoken": token,
            "wsfunction": "core_grades_update_grades",
            "moodlewsrestformat": "json",
            "component": "mod_quiz",
            "activityid": 1,
            "itemnumber": 0,
            "grades[0][studentid]": 5,
            "grades[0][grade]": 9.0,
        }
    },
    {
        "name": "Test 2 - with feedback format",
        "params": {
            "wstoken": token,
            "wsfunction": "core_grades_update_grades",
            "moodlewsrestformat": "json",
            "component": "mod_quiz",
            "activityid": 1,
            "itemnumber": 0,
            "grades[0][studentid]": 5,
            "grades[0][grade]": 9.0,
            "grades[0][str_feedback]": "AI graded",
            "grades[0][feedbackformat]": 0,
        }
    },
    {
        "name": "Test 3 - no itemnumber",
        "params": {
            "wstoken": token,
            "wsfunction": "core_grades_update_grades",
            "moodlewsrestformat": "json",
            "component": "mod_quiz",
            "activityid": 1,
            "grades[0][studentid]": 5,
            "grades[0][grade]": 9.0,
        }
    },
]

for test in test_cases:
    print(f"\n{test['name']}")
    r = requests.post(endpoint, data=test["params"])
    print(f"Response: {r.text[:300]}")
    
# Add these to your test script and run again

{
    "name": "Test 4 - quiz component string",
    "params": {
        "wstoken": token,
        "wsfunction": "core_grades_update_grades",
        "moodlewsrestformat": "json",
        "component": "quiz",
        "activityid": 1,
        "itemnumber": 0,
        "grades[0][studentid]": 5,
        "grades[0][grade]": 9.0,
    }
},
{
    "name": "Test 5 - GET instead of POST",
    "params": {
        "wstoken": token,
        "wsfunction": "core_grades_update_grades",
        "moodlewsrestformat": "json",
        "component": "mod_quiz",
        "activityid": 1,
        "itemnumber": 0,
        "grades[0][studentid]": 5,
        "grades[0][grade]": 9.0,
    }
},