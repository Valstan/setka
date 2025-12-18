-- Migration: Add sentiment analysis fields to posts table
-- Date: 2025-10-11
-- Purpose: Enable sentiment tracking for content balancing

-- Add sentiment fields
ALTER TABLE posts ADD COLUMN IF NOT EXISTS sentiment_label VARCHAR(20);
ALTER TABLE posts ADD COLUMN IF NOT EXISTS sentiment_score FLOAT;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS sentiment_emotions JSONB;

-- Create index for sentiment queries
CREATE INDEX IF NOT EXISTS idx_posts_sentiment ON posts(sentiment_label) WHERE sentiment_label IS NOT NULL;

-- Add comment
COMMENT ON COLUMN posts.sentiment_label IS 'Sentiment: positive, neutral, negative';
COMMENT ON COLUMN posts.sentiment_score IS 'Sentiment confidence score (0.0-1.0)';
COMMENT ON COLUMN posts.sentiment_emotions IS 'Emotion scores: {joy, sadness, anger, fear}';

-- Example query to get sentiment distribution
-- SELECT 
--     sentiment_label,
--     COUNT(*) as count,
--     AVG(sentiment_score) as avg_score
-- FROM posts
-- WHERE sentiment_label IS NOT NULL
-- GROUP BY sentiment_label;

