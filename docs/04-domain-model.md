# 4. Domain Model

Bounded contexts: **Questionnaire Authoring** (Phase 1), **Knowledge** (Phase 1),
**Response Collection** and **Analytics** (Phase 2), **Tenancy & Access** (Phase 3).

```mermaid
classDiagram
  direction LR
  class Questionnaire {
    <<Aggregate Root>>
    UUID id
    str title
    str description
    str survey_type
    QuestionnaireStatus status
    int version
    tuple~Question~ questions
    tuple~str~ source_template_ids
    revise(title, description, questions) Questionnaire
    publish() Questionnaire
  }
  class Question {
    <<Value Object>>
    str id
    str label
    str category
    str description
    AnswerType answer_type
    tuple~Choice~ choices
    tuple~Validation~ validations
    tuple~DependencyRule~ dependency_rules
    tuple~str~ business_tags
  }
  class Choice { str value; str label }
  class Validation { ValidationKind kind; value; message }
  class DependencyRule { str depends_on; operator; value; DependencyAction action }
  class QuestionTemplate {
    <<Knowledge>>
    str template_id
    str template_name
    Question question
    TemplateMetadata metadata
    to_document_text() str
  }
  class TemplateMetadata { industry; business_function; survey_type; question_tags }
  class BusinessIntent { survey_type; business_function; industry; keywords; question_count; confidence }
  class ValidationReport { tuple~ValidationIssue~ issues; is_valid }

  Questionnaire "1" *-- "*" Question
  Question *-- "*" Choice
  Question *-- "*" Validation
  Question *-- "*" DependencyRule
  QuestionTemplate *-- Question
  QuestionTemplate *-- TemplateMetadata
  Questionnaire ..> QuestionTemplate : lineage (source_template_ids)
```

## Invariants
| Invariant | Enforced in |
|---|---|
| Single/multiple choice questions have ≥ 2 choices; other types have none | `Question` validator |
| Only `draft` questionnaires can be revised or published | `Questionnaire._ensure_editable` |
| A published questionnaire has ≥ 1 question | `Questionnaire.publish` |
| Every revision increments `version` | `Questionnaire.revise` |
| Question ids are unique | `domain.validation.rule_unique_ids` |
| Skip logic references only *earlier* questions (prevents cycles) | `rule_dependencies_reference_earlier_questions` |

## Lifecycle
```mermaid
stateDiagram-v2
  [*] --> Draft : generated
  Draft --> Draft : revise (version+1)
  Draft --> Published : publish
  Published --> Archived : archive (Phase 2)
  Archived --> [*]
```

## Domain events
`QuestionnaireGenerated`, `QuestionnaireRevised`, `QuestionnairePublished`, `KnowledgeIngested`.
Phase 2 adds `ResponseSubmitted` and `AnalyticsComputed`.
