// API Client for SETKA
const apiClient = {
    baseUrl: '/api',
    
    // Generic request handler
    async request(endpoint, options = {}) {
        const url = `${this.baseUrl}${endpoint}`;
        const config = {
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            },
            ...options
        };
        
        try {
            const response = await fetch(url, config);
            
            if (!response.ok) {
                const error = await response.json().catch(() => ({ detail: response.statusText }));
                throw new Error(error.detail || `HTTP ${response.status}`);
            }
            
            return await response.json();
        } catch (error) {
            console.error('API Request failed:', error);
            throw error;
        }
    },
    
    // Health endpoints
    async getHealth() {
        return this.request('/health/');
    },
    
    async getFullHealth() {
        return this.request('/health/full');
    },
    
    // Regions endpoints
    async getRegions() {
        return this.request('/regions/');
    },
    
    async getRegion(code) {
        return this.request(`/regions/${code}`);
    },
    
    async createRegion(data) {
        return this.request('/regions/', {
            method: 'POST',
            body: JSON.stringify(data)
        });
    },
    
    async updateRegion(id, data) {
        return this.request(`/regions/${id}`, {
            method: 'PUT',
            body: JSON.stringify(data)
        });
    },
    
    async toggleRegionStatus(regionId) {
        return this.request(`/regions/${regionId}/toggle-status`, {
            method: 'PATCH'
        });
    },
    
    async deleteRegion(id) {
        return this.request(`/regions/${id}`, {
            method: 'DELETE'
        });
    },
    
    // Communities endpoints
    async getCommunities(params = {}) {
        // If region_id is specified, use region-specific endpoint
        if (params.region_id) {
            const regionId = params.region_id;
            delete params.region_id;
            return this.request(`/communities/region/${regionId}`);
        }
        
        // Otherwise, get all communities from all regions
        const regions = await this.getRegions();
        const allCommunities = [];
        for (const region of regions) {
            try {
                const communities = await this.request(`/communities/region/${region.id}`);
                allCommunities.push(...communities);
            } catch (err) {
                console.warn(`Failed to load communities for region ${region.id}:`, err);
            }
        }
        
        // Apply filters
        let filtered = allCommunities;
        if (params.category) {
            filtered = filtered.filter(c => c.category === params.category);
        }
        if (params.is_active !== undefined) {
            filtered = filtered.filter(c => c.is_active === (params.is_active === 'true'));
        }
        
        // Apply pagination
        const skip = parseInt(params.skip) || 0;
        const limit = parseInt(params.limit) || 100;
        return filtered.slice(skip, skip + limit);
    },
    
    async getCommunitiesByRegion(regionId) {
        return this.request(`/communities/region/${regionId}`);
    },
    
    async getCommunity(id) {
        return this.request(`/communities/${id}`);
    },
    
    async createCommunity(data) {
        return this.request('/communities/', {
            method: 'POST',
            body: JSON.stringify(data)
        });
    },
    
    async updateCommunity(id, data) {
        return this.request(`/communities/${id}`, {
            method: 'PUT',
            body: JSON.stringify(data)
        });
    },
    
    async deleteCommunity(id) {
        return this.request(`/communities/${id}`, {
            method: 'DELETE'
        });
    },
    
    // Posts endpoints
    async getPosts(params = {}) {
        const queryString = new URLSearchParams(params).toString();
        const endpoint = queryString ? `/posts/?${queryString}` : '/posts/';
        return this.request(endpoint);
    },
    
    async getPost(id) {
        return this.request(`/posts/${id}`);
    },
    
    // VK API monitoring endpoints
    async getVKStats() {
        return this.request('/vk/stats');
    },
    
    async validateVKTokens() {
        return this.request('/vk/validate-tokens');
    },
    
    async getVKCarouselStatus() {
        return this.request('/vk/carousel-status');
    },
    
    // Notifications endpoints
    async getNotifications() {
        return this.request('/notifications/');
    },
    
    async getSuggestedNotifications() {
        return this.request('/notifications/suggested');
    },
    
    async getMessagesNotifications() {
        return this.request('/notifications/messages');
    },
    
    async checkNotificationsNow() {
        return this.request('/notifications/check-now', {
            method: 'POST'
        });
    },
    
    async clearNotifications() {
        return this.request('/notifications/', {
            method: 'DELETE'
        });
    },
    
    // Stats helper
    async getStats() {
        try {
            const [regions, posts, communities] = await Promise.all([
                this.getRegions(),
                this.getPosts({ limit: 1 }),
                this.getCommunities({ limit: 1 })
            ]);
            
            return {
                regionsCount: regions.length,
                postsCount: posts.length,
                communitiesCount: communities.length
            };
        } catch (error) {
            console.error('Error getting stats:', error);
            return {
                regionsCount: 0,
                postsCount: 0,
                communitiesCount: 0
            };
        }
    }
};

// Export for use in other scripts
if (typeof module !== 'undefined' && module.exports) {
    module.exports = apiClient;
}

