-- SETKA Database Schema
-- Drop existing tables if they exist
DROP TABLE IF EXISTS publish_schedules CASCADE;
DROP TABLE IF EXISTS vk_tokens CASCADE;
DROP TABLE IF EXISTS filters CASCADE;
DROP TABLE IF EXISTS posts CASCADE;
DROP TABLE IF EXISTS communities CASCADE;
DROP TABLE IF EXISTS regions CASCADE;

-- Regions table
CREATE TABLE regions (
    id SERIAL PRIMARY KEY,
    code VARCHAR(50) UNIQUE NOT NULL,
    name VARCHAR(200) NOT NULL,
    vk_group_id INTEGER,
    telegram_channel VARCHAR(100),
    neighbors VARCHAR(500),
    local_hashtags TEXT,
    config JSON,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX ix_regions_code ON regions(code);

-- Communities table
CREATE TABLE communities (
    id SERIAL PRIMARY KEY,
    region_id INTEGER NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
    vk_id INTEGER NOT NULL,
    screen_name VARCHAR(100),
    name VARCHAR(300) NOT NULL,
    category VARCHAR(50) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    check_interval INTEGER DEFAULT 300,
    last_checked TIMESTAMP,
    last_post_id INTEGER,
    posts_count INTEGER DEFAULT 0,
    errors_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX ix_communities_region_category ON communities(region_id, category);
CREATE INDEX ix_communities_category ON communities(category);
CREATE INDEX ix_communities_vk_id_unique ON communities(vk_id);

-- Posts table
CREATE TABLE posts (
    id SERIAL PRIMARY KEY,
    region_id INTEGER NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
    community_id INTEGER NOT NULL REFERENCES communities(id) ON DELETE CASCADE,
    vk_post_id INTEGER NOT NULL,
    vk_owner_id INTEGER NOT NULL,
    text TEXT,
    attachments JSON,
    date_published TIMESTAMP NOT NULL,
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    reposts INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    ai_category VARCHAR(50),
    ai_relevance INTEGER,
    ai_score INTEGER,
    ai_analyzed BOOLEAN DEFAULT FALSE,
    ai_analysis_date TIMESTAMP,
    status VARCHAR(20) DEFAULT 'new',
    published_at TIMESTAMP,
    published_vk BOOLEAN DEFAULT FALSE,
    published_telegram BOOLEAN DEFAULT FALSE,
    published_wordpress BOOLEAN DEFAULT FALSE,
    is_duplicate BOOLEAN DEFAULT FALSE,
    is_spam BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX ix_posts_vk_id_composite ON posts(vk_owner_id, vk_post_id);
CREATE INDEX ix_posts_status_idx ON posts(status);
CREATE INDEX ix_posts_region_status_composite ON posts(region_id, status);
CREATE INDEX ix_posts_date_idx ON posts(date_published);

-- Filters table  
CREATE TABLE filters (
    id SERIAL PRIMARY KEY,
    type VARCHAR(50) NOT NULL,
    category VARCHAR(50),
    pattern TEXT NOT NULL,
    action VARCHAR(20) NOT NULL,
    score_modifier INTEGER DEFAULT 0,
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX ix_filters_type ON filters(type);

-- VK Tokens table
CREATE TABLE vk_tokens (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) UNIQUE NOT NULL,
    token TEXT NOT NULL,
    usage_type VARCHAR(20) NOT NULL,
    requests_count INTEGER DEFAULT 0,
    last_request TIMESTAMP,
    rate_limit_reset TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    errors_count INTEGER DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Publish Schedules table
CREATE TABLE publish_schedules (
    id SERIAL PRIMARY KEY,
    region_id INTEGER NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
    category VARCHAR(50) NOT NULL,
    hour INTEGER NOT NULL,
    minute INTEGER NOT NULL,
    days_of_week VARCHAR(20) DEFAULT '0,1,2,3,4,5,6',
    is_active BOOLEAN DEFAULT TRUE,
    last_run TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Grant permissions
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO setka_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO setka_user;

