-- workflow_uniapp MySQL Schema

CREATE TABLE IF NOT EXISTS workflow_session (
    session_id      VARCHAR(12)  PRIMARY KEY,
    app_name        VARCHAR(60)  NOT NULL,
    project_dir     VARCHAR(40)  NOT NULL,
    framework       VARCHAR(20)  DEFAULT 'vue',
    llm_model       VARCHAR(100) DEFAULT '',
    step            VARCHAR(30)  NOT NULL DEFAULT 'input',
    error           TEXT,
    error_step      VARCHAR(30)  DEFAULT '',
    project_path    VARCHAR(500) DEFAULT '',
    selected_task_index INT     DEFAULT 0,
    quality_score   INT         DEFAULT 0,
    created_at      DATETIME    DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS workflow_log (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id      VARCHAR(12) NOT NULL,
    message         TEXT        NOT NULL,
    created_at      DATETIME   DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_session (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS style_task (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id      VARCHAR(12) NOT NULL,
    style_id        VARCHAR(50) NOT NULL,
    style_name      VARCHAR(100) NOT NULL,
    color_hint      VARCHAR(50) DEFAULT '',
    project_id      VARCHAR(100) DEFAULT '',
    status          VARCHAR(20) DEFAULT 'pending',
    error           TEXT,
    skip_design     BOOLEAN    DEFAULT FALSE,
    INDEX idx_session (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS screen_ref (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    task_id         BIGINT     NOT NULL,
    page_key        VARCHAR(10) NOT NULL,
    screen_id       VARCHAR(100) NOT NULL,
    FOREIGN KEY (task_id) REFERENCES style_task(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS quality_check (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id      VARCHAR(12) NOT NULL,
    label           VARCHAR(200) NOT NULL,
    passed          BOOLEAN    NOT NULL,
    detail          VARCHAR(500) DEFAULT '',
    INDEX idx_session (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS user_feedback (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id      VARCHAR(12) NOT NULL UNIQUE,
    rating          VARCHAR(20) DEFAULT '',
    score           INT        DEFAULT 0,
    comment         TEXT,
    created_at      DATETIME   DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS error_stat (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    error_key       VARCHAR(100) NOT NULL UNIQUE,
    count           INT        DEFAULT 0,
    last_seen       DATETIME,
    sample          TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
