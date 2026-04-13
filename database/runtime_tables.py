from sqlalchemy import text

from database.database import get_engine


async def ensure_runtime_tables() -> None:
    statements = [
        """
        CREATE TABLE IF NOT EXISTS notification_reads (
            notification_read_id BIGINT AUTO_INCREMENT PRIMARY KEY,
            user_id BIGINT NOT NULL,
            course_id BIGINT NOT NULL,
            notification_key VARCHAR(64) NOT NULL,
            read_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY unique_notification_read_user_course_key (
                user_id,
                course_id,
                notification_key
            ),
            KEY idx_notification_reads_user_course (user_id, course_id),
            CONSTRAINT fk_notification_reads_user
                FOREIGN KEY (user_id) REFERENCES users(user_id),
            CONSTRAINT fk_notification_reads_course
                FOREIGN KEY (course_id) REFERENCES courses(course_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS quiz_attempts (
            quiz_attempt_id BIGINT AUTO_INCREMENT PRIMARY KEY,
            course_id BIGINT NOT NULL,
            enrollment_id BIGINT NOT NULL,
            message_key VARCHAR(64) NOT NULL,
            selected_index INT NOT NULL,
            is_correct BOOLEAN NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY unique_quiz_attempt_enrollment_course_message (
                enrollment_id,
                course_id,
                message_key
            ),
            KEY idx_quiz_attempts_course_enrollment (course_id, enrollment_id),
            CONSTRAINT fk_quiz_attempts_course
                FOREIGN KEY (course_id) REFERENCES courses(course_id),
            CONSTRAINT fk_quiz_attempts_enrollment
                FOREIGN KEY (enrollment_id) REFERENCES enrollments(enrollment_id)
        )
        """,
    ]
    async with get_engine().begin() as connection:
        for statement in statements:
            await connection.execute(text(statement))
