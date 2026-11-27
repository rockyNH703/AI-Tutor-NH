// GENERATED from docs/openapi.json by scripts/gen-contract.ts. Do not edit: run `bun run contract`.
// The backend owns these enums and limits; the web app reads them from here.

export const CONTRACT = {
  "ActivityTimeOut": {
    "activity": {
      "enum": [
        "reading",
        "practice",
        "revision",
        "test"
      ]
    }
  },
  "AppearanceOut": {
    "fontSize": {
      "enum": [
        "small",
        "medium",
        "large",
        "x_large"
      ]
    },
    "theme": {
      "enum": [
        "system",
        "light",
        "dark"
      ]
    }
  },
  "AppearanceSet": {
    "fontSize": {
      "enum": [
        "small",
        "medium",
        "large",
        "x_large"
      ]
    },
    "theme": {
      "enum": [
        "system",
        "light",
        "dark"
      ]
    }
  },
  "ApproveProposalRequest": {
    "expectedVersion": {
      "minimum": 1
    },
    "kind": {
      "enum": [
        "learning_context",
        "study_context"
      ]
    }
  },
  "AssessmentAnswerOut": {
    "status": {
      "enum": [
        "answered",
        "skipped"
      ]
    }
  },
  "AssessmentQuestionOut": {
    "kind": {
      "enum": [
        "mcq",
        "multi",
        "fill_blank",
        "numeric",
        "short",
        "long",
        "match",
        "order",
        "cli",
        "lines",
        "diagram"
      ]
    },
    "select": {
      "enum": [
        "node",
        "link",
        "any"
      ]
    }
  },
  "AssessmentResponseOut": {
    "kind": {
      "enum": [
        "test",
        "exercise"
      ]
    },
    "mode": {
      "enum": [
        "practice",
        "exam"
      ]
    },
    "state": {
      "enum": [
        "not_started",
        "active",
        "completed"
      ]
    }
  },
  "AssessmentResponseRequest": {
    "action": {
      "enum": [
        "answer",
        "skip",
        "save",
        "clear",
        "submit"
      ]
    },
    "hash": {
      "pattern": "^[0-9a-f]{64}$"
    },
    "position": {
      "maximum": 9,
      "minimum": 0
    },
    "question": {
      "minimum": 0
    },
    "revision": {
      "minimum": 0
    }
  },
  "ChaptersOut": {
    "reason": {
      "enum": [
        "no_class",
        "unavailable",
        "not_configured"
      ]
    }
  },
  "ChatRequest": {
    "attachmentIds": {
      "maxItems": 5
    },
    "message": {
      "maxLength": 16000,
      "minLength": 1
    },
    "mode": {
      "enum": [
        "text",
        "voice"
      ]
    },
    "month": {
      "maximum": 12,
      "minimum": 1
    },
    "program": {
      "maxLength": 60,
      "minLength": 1,
      "pattern": "^[a-z0-9]+(-[a-z0-9]+)*$"
    },
    "speechFormat": {
      "enum": [
        "mp3",
        "opus",
        "aac"
      ]
    },
    "tutorMode": {
      "enum": [
        "tutor",
        "interview"
      ]
    }
  },
  "ClaimOut": {
    "status": {
      "enum": [
        "claimed",
        "already"
      ]
    }
  },
  "ClaimRequest": {
    "invoiceNumber": {
      "maxLength": 80,
      "minLength": 1
    }
  },
  "ConceptMasteryOut": {
    "state": {
      "enum": [
        "mastered",
        "learning",
        "weak",
        "not_enough_evidence"
      ]
    }
  },
  "CorrectMemoryRequest": {
    "expectedVersion": {
      "minimum": 1
    },
    "kind": {
      "enum": [
        "learning_context",
        "study_context"
      ]
    }
  },
  "CreateConversationRequest": {
    "title": {
      "maxLength": 120
    }
  },
  "CreateMemoryRequest": {
    "kind": {
      "enum": [
        "learning_context",
        "study_context"
      ]
    }
  },
  "DeleteMeRequest": {
    "password": {
      "maxLength": 128,
      "minLength": 1
    }
  },
  "DeleteMemoryRequest": {
    "expectedVersion": {
      "minimum": 1
    }
  },
  "EmailChangeRequest": {
    "password": {
      "maxLength": 128,
      "minLength": 1
    }
  },
  "EmailConfirmRequest": {
    "code": {
      "pattern": "^\\d{6}$"
    }
  },
  "EngineeringProfileSet": {
    "certificationGoal": {
      "maxLength": 40,
      "minLength": 1,
      "pattern": "^[a-z0-9_]+$"
    },
    "experienceLevel": {
      "maxLength": 40,
      "minLength": 1,
      "pattern": "^[a-z0-9_]+$"
    },
    "focusAreas": {
      "items": {
        "maxLength": 40,
        "minLength": 1,
        "pattern": "^[a-z0-9_]+$"
      },
      "maxItems": 8
    },
    "platforms": {
      "items": {
        "maxLength": 40,
        "minLength": 1,
        "pattern": "^[a-z0-9_]+$"
      },
      "maxItems": 12
    },
    "role": {
      "maxLength": 40,
      "minLength": 1,
      "pattern": "^[a-z0-9_]+$"
    }
  },
  "ExamOut": {
    "status": {
      "enum": [
        "in_progress",
        "submitted"
      ]
    }
  },
  "ExamStartRequest": {
    "difficulty": {
      "enum": [
        "mixed",
        "easy",
        "medium",
        "hard"
      ]
    },
    "length": {
      "maximum": 30,
      "minimum": 1
    },
    "minutes": {
      "maximum": 60,
      "minimum": 1
    },
    "month": {
      "maximum": 12,
      "minimum": 1
    },
    "program": {
      "maxLength": 60,
      "pattern": "^[a-z0-9]+(-[a-z0-9]+)*$"
    },
    "topicIds": {
      "items": {
        "maxLength": 120,
        "minLength": 1
      },
      "maxItems": 50,
      "minItems": 1
    }
  },
  "FeedbackOut": {
    "rating": {
      "enum": [
        "up",
        "down"
      ]
    }
  },
  "FeedbackRequest": {
    "comment": {
      "maxLength": 2000
    },
    "rating": {
      "enum": [
        "up",
        "down"
      ]
    }
  },
  "FileOut": {
    "kind": {
      "enum": [
        "pdf",
        "docx",
        "pptx",
        "xlsx",
        "csv",
        "txt",
        "md",
        "image"
      ]
    },
    "status": {
      "enum": [
        "processing",
        "ready",
        "failed",
        "stored"
      ]
    }
  },
  "FriendZoneAnswerRequest": {
    "choice": {
      "pattern": "^[A-H]$"
    },
    "round": {
      "maximum": 10,
      "minimum": 1
    }
  },
  "FriendZoneBattleOut": {
    "format": {
      "enum": [
        "rounds",
        "exam"
      ]
    },
    "result": {
      "enum": [
        "points",
        "forfeit",
        "draw",
        "void"
      ]
    },
    "status": {
      "enum": [
        "invited",
        "waiting",
        "active",
        "completed",
        "declined",
        "expired",
        "cancelled"
      ]
    }
  },
  "FriendZoneBattlePlayerOut": {
    "role": {
      "enum": [
        "challenger",
        "opponent"
      ]
    }
  },
  "FriendZoneBattleResponsesOut": {
    "status": {
      "enum": [
        "invited",
        "waiting",
        "active",
        "completed",
        "declined",
        "expired",
        "cancelled"
      ]
    }
  },
  "FriendZoneBattleResponsesRequest": {
    "responses": {
      "maxItems": 30,
      "minItems": 1
    }
  },
  "FriendZoneBattleSummaryOut": {
    "result": {
      "enum": [
        "points",
        "forfeit",
        "draw",
        "void"
      ]
    },
    "role": {
      "enum": [
        "challenger",
        "opponent"
      ]
    },
    "status": {
      "enum": [
        "invited",
        "waiting",
        "active",
        "completed",
        "declined",
        "expired",
        "cancelled"
      ]
    }
  },
  "FriendZoneCreateBattleRequest": {
    "difficulty": {
      "enum": [
        "mixed",
        "easy",
        "medium",
        "hard"
      ]
    },
    "length": {
      "enum": [
        10,
        20,
        30
      ]
    },
    "month": {
      "maximum": 12,
      "minimum": 1
    },
    "program": {
      "maxLength": 60,
      "minLength": 1
    },
    "topicId": {
      "maxLength": 120
    }
  },
  "FriendZoneDailyAnswerRequest": {
    "choice": {
      "pattern": "^[A-H]$"
    },
    "position": {
      "maximum": 10,
      "minimum": 1
    }
  },
  "FriendZoneDailyAttemptOut": {
    "status": {
      "enum": [
        "not_started",
        "in_progress",
        "finished"
      ]
    }
  },
  "FriendZoneDailyFriendOut": {
    "status": {
      "enum": [
        "completed",
        "in_progress"
      ]
    }
  },
  "FriendZoneJoinRequest": {
    "displayName": {
      "maxLength": 60,
      "minLength": 1
    }
  },
  "FriendZonePlayerOut": {
    "relation": {
      "enum": [
        "self",
        "friend",
        "incoming",
        "outgoing",
        "blocked",
        "none"
      ]
    }
  },
  "FriendZoneProfileChanges": {
    "displayName": {
      "maxLength": 60,
      "minLength": 1
    }
  },
  "FriendZoneReportRequest": {
    "details": {
      "maxLength": 1000
    },
    "reason": {
      "enum": [
        "harassment",
        "cheating",
        "inappropriate_name",
        "spam",
        "other"
      ]
    }
  },
  "FriendZoneResponseItem": {
    "baseRev": {
      "maximum": 2147483647,
      "minimum": 0
    },
    "choice": {
      "pattern": "^[A-H]$"
    },
    "op": {
      "enum": [
        "set",
        "visit"
      ]
    },
    "round": {
      "maximum": 30,
      "minimum": 1
    }
  },
  "FriendZoneSearchRequest": {
    "invoiceNumber": {
      "maxLength": 60,
      "minLength": 1
    }
  },
  "FriendZoneSubmitRequest": {
    "responses": {
      "maxItems": 30
    }
  },
  "LearnerProfileOut": {
    "mainGoal": {
      "enum": [
        "board_exams",
        "competitive_exam",
        "better_marks",
        "understand_concepts",
        "homework_help",
        "other"
      ]
    }
  },
  "LearnerProfileSet": {
    "academicYear": {
      "maxLength": 40,
      "minLength": 1,
      "pattern": "^[a-z0-9_]+$"
    },
    "board": {
      "maxLength": 40,
      "minLength": 1,
      "pattern": "^[a-z0-9_]+$"
    },
    "boardState": {
      "maxLength": 40,
      "minLength": 1,
      "pattern": "^[a-z0-9_]+$"
    },
    "dailyActiveMinutesGoal": {
      "maximum": 720,
      "minimum": 0
    },
    "grade": {
      "maxLength": 40,
      "minLength": 1,
      "pattern": "^[a-z0-9_]+$"
    },
    "longTermGoal": {
      "maxLength": 280,
      "minLength": 1
    },
    "mainGoal": {
      "enum": [
        "board_exams",
        "competitive_exam",
        "better_marks",
        "understand_concepts",
        "homework_help",
        "other"
      ]
    },
    "medium": {
      "maxLength": 40,
      "minLength": 1,
      "pattern": "^[a-z0-9_]+$"
    },
    "preferredStudyTime": {
      "pattern": "^([01]\\d|2[0-3]):[0-5]\\d$"
    },
    "shortTermGoal": {
      "maxLength": 280,
      "minLength": 1
    },
    "targetExam": {
      "maxLength": 80,
      "minLength": 1
    },
    "targetScorePercent": {
      "maximum": 100,
      "minimum": 0
    },
    "weakTopics": {
      "items": {
        "maxLength": 60,
        "minLength": 1
      },
      "maxItems": 10
    },
    "weeklyActiveMinutesGoal": {
      "maximum": 5040,
      "minimum": 0
    }
  },
  "LearningDayOut": {
    "intensity": {
      "maximum": 4,
      "minimum": 1
    }
  },
  "LearningEventOut": {
    "event": {
      "enum": [
        "started",
        "extended",
        "milestone"
      ]
    }
  },
  "LearningPreferencesOut": {
    "answerMode": {
      "enum": [
        "auto",
        "direct",
        "hints_first",
        "socratic"
      ]
    },
    "askBeforeAnswer": {
      "enum": [
        "auto",
        "on",
        "off"
      ]
    },
    "difficulty": {
      "enum": [
        "auto",
        "easy",
        "medium",
        "hard"
      ]
    },
    "explanationLevel": {
      "enum": [
        "auto",
        "simple",
        "standard",
        "detailed"
      ]
    },
    "highlightKeyConcepts": {
      "enum": [
        "auto",
        "on",
        "off"
      ]
    },
    "language": {
      "enum": [
        "auto",
        "english",
        "hindi",
        "hinglish"
      ]
    },
    "mistakeCorrection": {
      "enum": [
        "auto",
        "gentle",
        "direct"
      ]
    },
    "pace": {
      "enum": [
        "auto",
        "slow",
        "steady",
        "fast"
      ]
    },
    "personality": {
      "enum": [
        "auto",
        "friendly",
        "encouraging",
        "professional"
      ]
    },
    "practiceQuestions": {
      "enum": [
        "auto",
        "on",
        "off"
      ]
    },
    "realWorldExamples": {
      "enum": [
        "auto",
        "more",
        "fewer"
      ]
    },
    "responseLength": {
      "enum": [
        "auto",
        "short",
        "medium",
        "long"
      ]
    },
    "stepByStep": {
      "enum": [
        "auto",
        "on",
        "off"
      ]
    },
    "visualExplanations": {
      "enum": [
        "auto",
        "more",
        "fewer"
      ]
    }
  },
  "LearningPreferencesSet": {
    "answerMode": {
      "enum": [
        "auto",
        "direct",
        "hints_first",
        "socratic"
      ]
    },
    "askBeforeAnswer": {
      "enum": [
        "auto",
        "on",
        "off"
      ]
    },
    "difficulty": {
      "enum": [
        "auto",
        "easy",
        "medium",
        "hard"
      ]
    },
    "explanationLevel": {
      "enum": [
        "auto",
        "simple",
        "standard",
        "detailed"
      ]
    },
    "highlightKeyConcepts": {
      "enum": [
        "auto",
        "on",
        "off"
      ]
    },
    "language": {
      "enum": [
        "auto",
        "english",
        "hindi",
        "hinglish"
      ]
    },
    "mistakeCorrection": {
      "enum": [
        "auto",
        "gentle",
        "direct"
      ]
    },
    "pace": {
      "enum": [
        "auto",
        "slow",
        "steady",
        "fast"
      ]
    },
    "personality": {
      "enum": [
        "auto",
        "friendly",
        "encouraging",
        "professional"
      ]
    },
    "practiceQuestions": {
      "enum": [
        "auto",
        "on",
        "off"
      ]
    },
    "realWorldExamples": {
      "enum": [
        "auto",
        "more",
        "fewer"
      ]
    },
    "responseLength": {
      "enum": [
        "auto",
        "short",
        "medium",
        "long"
      ]
    },
    "stepByStep": {
      "enum": [
        "auto",
        "on",
        "off"
      ]
    },
    "visualExplanations": {
      "enum": [
        "auto",
        "more",
        "fewer"
      ]
    }
  },
  "MarkReadRequest": {
    "ids": {
      "maxItems": 30
    }
  },
  "MemoryOut": {
    "kind": {
      "enum": [
        "learning_context",
        "study_context"
      ]
    }
  },
  "MistakeConceptOut": {
    "state": {
      "enum": [
        "mastered",
        "learning",
        "weak",
        "not_enough_evidence"
      ]
    }
  },
  "MistakeOut": {
    "chosen": {
      "enum": [
        "A",
        "B",
        "C",
        "D",
        "E",
        "F",
        "G",
        "H"
      ]
    },
    "chosenKeys": {
      "items": {
        "enum": [
          "A",
          "B",
          "C",
          "D",
          "E",
          "F",
          "G",
          "H"
        ]
      }
    },
    "correct": {
      "enum": [
        "A",
        "B",
        "C",
        "D",
        "E",
        "F",
        "G",
        "H"
      ]
    },
    "correctKeys": {
      "items": {
        "enum": [
          "A",
          "B",
          "C",
          "D",
          "E",
          "F",
          "G",
          "H"
        ]
      }
    }
  },
  "NhprepItemOut": {
    "kind": {
      "enum": [
        "note",
        "flashcard",
        "cheat_sheet",
        "interview_question"
      ]
    }
  },
  "NhprepLessonOut": {
    "video": {
      "enum": [
        "ready",
        "no_video",
        "not_configured"
      ]
    }
  },
  "NhprepPlaybackOut": {
    "reason": {
      "enum": [
        "not_configured",
        "no_video",
        "signing_failed"
      ]
    },
    "status": {
      "enum": [
        "ready",
        "unavailable"
      ]
    }
  },
  "NotificationPreferencesOut": {
    "delivery": {
      "enum": [
        "active",
        "paused"
      ]
    }
  },
  "PracticeBookOut": {
    "language": {
      "enum": [
        "english",
        "hindi",
        "sanskrit"
      ]
    }
  },
  "PracticeExamResponseItem": {
    "index": {
      "minimum": 0
    },
    "keys": {
      "items": {
        "pattern": "^[A-H]$"
      },
      "maxItems": 8
    }
  },
  "PracticeExamSaveRequest": {
    "baseRev": {
      "maximum": 2147483647,
      "minimum": 0
    },
    "items": {
      "maxItems": 30,
      "minItems": 1
    }
  },
  "PracticeExamSubmitRequest": {
    "baseRev": {
      "maximum": 2147483647,
      "minimum": 0
    },
    "items": {
      "maxItems": 30
    }
  },
  "PracticePlanOut": {
    "budgetSource": {
      "enum": [
        "goal",
        "default"
      ]
    }
  },
  "PracticeTaskOut": {
    "kind": {
      "enum": [
        "revise",
        "strengthen",
        "learn"
      ]
    },
    "language": {
      "enum": [
        "english",
        "hindi",
        "sanskrit"
      ]
    },
    "reason": {
      "enum": [
        "overdue",
        "due",
        "weak_concept",
        "next_topic",
        "start_here"
      ]
    }
  },
  "PracticeTopicsOut": {
    "language": {
      "enum": [
        "english",
        "hindi"
      ]
    },
    "reason": {
      "enum": [
        "no_class",
        "no_questions"
      ]
    }
  },
  "ProfileSet": {
    "displayName": {
      "maxLength": 80,
      "minLength": 1
    },
    "timezone": {
      "maxLength": 64
    }
  },
  "ProgramClaimOut": {
    "state": {
      "enum": [
        "active",
        "expired",
        "revoked"
      ]
    }
  },
  "ProposalOut": {
    "kind": {
      "enum": [
        "learning_context",
        "study_context"
      ]
    }
  },
  "RecentSessionOut": {
    "activities": {
      "items": {
        "enum": [
          "reading",
          "practice",
          "revision",
          "test"
        ]
      }
    },
    "endReason": {
      "enum": [
        "ended",
        "stale",
        "limit"
      ]
    },
    "status": {
      "enum": [
        "active",
        "paused",
        "ended"
      ]
    }
  },
  "RejectProposalRequest": {
    "expectedVersion": {
      "minimum": 1
    }
  },
  "ReplaceSubjectsRequest": {
    "items": {
      "maxItems": 15
    },
    "version": {
      "minimum": 0
    }
  },
  "ReviewOut": {
    "outcome": {
      "enum": [
        "correct",
        "incorrect",
        "unanswered",
        "void"
      ]
    }
  },
  "RevisionOut": {
    "state": {
      "enum": [
        "overdue",
        "due",
        "done",
        "upcoming"
      ]
    }
  },
  "SegmentOut": {
    "activity": {
      "enum": [
        "reading",
        "practice",
        "revision",
        "test"
      ]
    },
    "kind": {
      "enum": [
        "study",
        "paused"
      ]
    }
  },
  "SpeechRequest": {
    "format": {
      "enum": [
        "mp3",
        "opus",
        "aac"
      ]
    },
    "text": {
      "maxLength": 600,
      "minLength": 1
    }
  },
  "StudyContextChangeIn": {
    "activity": {
      "enum": [
        "reading",
        "practice",
        "revision",
        "test"
      ]
    },
    "chapterId": {
      "maxLength": 120
    },
    "subject": {
      "maxLength": 40
    }
  },
  "StudySessionOut": {
    "endReason": {
      "enum": [
        "ended",
        "stale",
        "limit"
      ]
    },
    "status": {
      "enum": [
        "active",
        "paused",
        "ended"
      ]
    }
  },
  "StudyStartIn": {
    "activity": {
      "enum": [
        "reading",
        "practice",
        "revision",
        "test"
      ]
    },
    "chapterId": {
      "maxLength": 120
    },
    "subject": {
      "maxLength": 40
    }
  },
  "SubjectIn": {
    "code": {
      "maxLength": 40,
      "minLength": 1,
      "pattern": "^[a-z0-9_]+$"
    }
  },
  "SubjectMasteryOut": {
    "language": {
      "enum": [
        "english",
        "hindi",
        "sanskrit"
      ]
    }
  },
  "SubmittedOut": {
    "status": {
      "enum": [
        "in_progress",
        "submitted"
      ]
    }
  },
  "SupportRequestIn": {
    "kind": {
      "enum": [
        "question",
        "bug",
        "feedback",
        "account"
      ]
    },
    "message": {
      "maxLength": 4000,
      "minLength": 10
    },
    "page": {
      "maxLength": 200,
      "pattern": "^/[^\\s]*$"
    },
    "subject": {
      "maxLength": 120,
      "minLength": 1
    }
  },
  "SupportRequestOut": {
    "kind": {
      "enum": [
        "question",
        "bug",
        "feedback",
        "account"
      ]
    },
    "status": {
      "enum": [
        "open",
        "closed"
      ]
    }
  },
  "TopicMasteryOut": {
    "state": {
      "enum": [
        "mastered",
        "learning",
        "weak",
        "not_enough_evidence"
      ]
    }
  },
  "TutorPreviewRequest": {
    "subjects": {
      "maxItems": 15
    }
  },
  "UnsubscribeRequest": {
    "token": {
      "maxLength": 300,
      "minLength": 10
    }
  },
  "UpdateAppearanceRequest": {
    "reset": {
      "items": {
        "enum": [
          "theme",
          "fontSize",
          "highContrast",
          "reduceMotion",
          "readingComfort",
          "showStreak"
        ]
      },
      "maxItems": 6
    },
    "version": {
      "minimum": 0
    }
  },
  "UpdateConversationRequest": {
    "title": {
      "maxLength": 120,
      "minLength": 1
    }
  },
  "UpdateEngineeringProfileRequest": {
    "reset": {
      "items": {
        "enum": [
          "role",
          "experienceLevel",
          "focusAreas",
          "platforms",
          "certificationGoal"
        ]
      },
      "maxItems": 5
    },
    "version": {
      "minimum": 0
    }
  },
  "UpdateFriendZoneProfileRequest": {
    "version": {
      "minimum": 0
    }
  },
  "UpdateLearnerProfileRequest": {
    "reset": {
      "items": {
        "enum": [
          "grade",
          "board",
          "boardState",
          "medium",
          "academicYear",
          "mainGoal",
          "targetExam",
          "targetScorePercent",
          "weakTopics",
          "dailyActiveMinutesGoal",
          "weeklyActiveMinutesGoal",
          "preferredStudyTime",
          "shortTermGoal",
          "longTermGoal",
          "examDate"
        ]
      },
      "maxItems": 15
    },
    "version": {
      "minimum": 0
    }
  },
  "UpdateLearningPreferencesRequest": {
    "reset": {
      "items": {
        "enum": [
          "language",
          "explanationLevel",
          "pace",
          "difficulty",
          "realWorldExamples",
          "visualExplanations",
          "personality",
          "responseLength",
          "answerMode",
          "stepByStep",
          "askBeforeAnswer",
          "practiceQuestions",
          "mistakeCorrection",
          "highlightKeyConcepts"
        ]
      },
      "maxItems": 14
    },
    "version": {
      "minimum": 0
    }
  },
  "UpdateMeRequest": {
    "displayName": {
      "maxLength": 80
    }
  },
  "UpdateMemoryPreferencesRequest": {
    "expectedVersion": {
      "minimum": 0
    }
  },
  "UpdateNotificationPreferencesRequest": {
    "reset": {
      "items": {
        "enum": [
          "dailyReminder",
          "streakWarning",
          "weeklySummary",
          "securityAlerts",
          "inAppReminders"
        ]
      },
      "maxItems": 5
    },
    "version": {
      "minimum": 0
    }
  },
  "UpdateProfileRequest": {
    "reset": {
      "items": {
        "enum": [
          "displayName",
          "timezone"
        ]
      },
      "maxItems": 2
    },
    "version": {
      "minimum": 0
    }
  }
} as const;
