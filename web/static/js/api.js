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

    // Ad cabinet endpoints
    async getAdRequests(params = {}) {
        const q = new URLSearchParams();
        if (params.status) q.set('status', params.status);
        if (params.origin) q.set('origin', params.origin);
        if (params.community_vk_id) q.set('community_vk_id', params.community_vk_id);
        if (params.date_from) q.set('date_from', params.date_from);
        if (params.date_to) q.set('date_to', params.date_to);
        const qs = q.toString();
        return this.request(`/ad-cabinet/requests${qs ? '?' + qs : ''}`);
    },

    async getAdThread(id, count = 30) {
        return this.request(`/ad-cabinet/requests/${id}/thread?count=${count}`);
    },

    async prepareAdReply(id, templateId) {
        return this.request(`/ad-cabinet/requests/${id}/prepare`, {
            method: 'POST',
            body: JSON.stringify({ template_id: templateId })
        });
    },

    async sendAdReply(id, payload = {}) {
        return this.request(`/ad-cabinet/requests/${id}/send`, {
            method: 'POST',
            body: JSON.stringify({
                message: payload.message ?? null,
                images: payload.images ?? null
            })
        });
    },

    async setAdStatus(id, status) {
        return this.request(`/ad-cabinet/requests/${id}/status`, {
            method: 'POST',
            body: JSON.stringify({ status })
        });
    },

    async bulkAdAction(ids, action, status = null) {
        return this.request('/ad-cabinet/requests/bulk-action', {
            method: 'POST',
            body: JSON.stringify({ ids, action, status })
        });
    },

    async getAdTemplates() {
        return this.request('/templates/?include_inactive=0');
    },

    // Ad cabinet — библиотека офферных картинок
    async getOfferImages() {
        return this.request('/ad-cabinet/offer-images');
    },

    async uploadOfferImage(file) {
        const fd = new FormData();
        fd.append('file', file);
        // Без Content-Type вручную — браузер сам проставит multipart boundary.
        const resp = await fetch(`${this.baseUrl}/ad-cabinet/offer-images`, {
            method: 'POST',
            body: fd
        });
        if (!resp.ok) {
            const e = await resp.json().catch(() => ({ detail: resp.statusText }));
            throw new Error(e.detail || `HTTP ${resp.status}`);
        }
        return resp.json();
    },

    async deleteOfferImage(name) {
        return this.request(`/ad-cabinet/offer-images/${encodeURIComponent(name)}`, {
            method: 'DELETE'
        });
    },

    // Ad cabinet — планировщик отложенных постов
    async getScheduledPosts(params = {}) {
        const q = new URLSearchParams();
        if (params.community_vk_id) q.set('community_vk_id', params.community_vk_id);
        if (params.status) q.set('status', params.status);
        if (params.date_from) q.set('date_from', params.date_from);
        if (params.date_to) q.set('date_to', params.date_to);
        const qs = q.toString();
        return this.request(`/ad-cabinet/scheduled${qs ? '?' + qs : ''}`);
    },

    async createScheduledPosts(payload) {
        return this.request('/ad-cabinet/scheduled', {
            method: 'POST',
            body: JSON.stringify(payload)
        });
    },

    async cancelScheduledPost(id) {
        return this.request(`/ad-cabinet/scheduled/${id}/cancel`, {
            method: 'POST'
        });
    },

    // Ad cabinet — CRM (блок C): клиенты / оплаты / публикации
    async getCrmFunnel() {
        return this.request('/ad-crm/funnel');
    },

    async getCrmClients(params = {}) {
        const q = new URLSearchParams();
        if (params.stage) q.set('stage', params.stage);
        if (params.region_id) q.set('region_id', params.region_id);
        if (params.q) q.set('q', params.q);
        const qs = q.toString();
        return this.request(`/ad-crm/clients${qs ? '?' + qs : ''}`);
    },

    async getCrmClient(id) {
        return this.request(`/ad-crm/clients/${id}`);
    },

    async createCrmClient(payload) {
        return this.request('/ad-crm/clients', {
            method: 'POST',
            body: JSON.stringify(payload)
        });
    },

    async updateCrmClient(id, payload) {
        return this.request(`/ad-crm/clients/${id}`, {
            method: 'PATCH',
            body: JSON.stringify(payload)
        });
    },

    async deleteCrmClient(id) {
        return this.request(`/ad-crm/clients/${id}`, { method: 'DELETE' });
    },

    async upsertCrmFromRequest(requestId) {
        return this.request(`/ad-crm/clients/upsert-from-request/${requestId}`, {
            method: 'POST'
        });
    },

    async createCrmPayment(payload) {
        return this.request('/ad-crm/payments', {
            method: 'POST',
            body: JSON.stringify(payload)
        });
    },

    async updateCrmPayment(id, payload) {
        return this.request(`/ad-crm/payments/${id}`, {
            method: 'PATCH',
            body: JSON.stringify(payload)
        });
    },

    async deleteCrmPayment(id) {
        return this.request(`/ad-crm/payments/${id}`, { method: 'DELETE' });
    },

    async getCrmBanks() {
        return this.request('/ad-crm/banks');
    },

    async createCrmPublication(payload) {
        return this.request('/ad-crm/publications', {
            method: 'POST',
            body: JSON.stringify(payload)
        });
    },

    async deleteCrmPublication(id) {
        return this.request(`/ad-crm/publications/${id}`, { method: 'DELETE' });
    },

    // CRM — таймлайн взаимодействий (PR-2)
    async getCrmTimeline(clientId) {
        return this.request(`/ad-crm/clients/${clientId}/timeline`);
    },

    async createCrmInteraction(payload) {
        return this.request('/ad-crm/interactions', {
            method: 'POST',
            body: JSON.stringify(payload)
        });
    },

    async updateCrmInteraction(id, payload) {
        return this.request(`/ad-crm/interactions/${id}`, {
            method: 'PATCH',
            body: JSON.stringify(payload)
        });
    },

    async deleteCrmInteraction(id) {
        return this.request(`/ad-crm/interactions/${id}`, { method: 'DELETE' });
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
        let communities = [];

        if (params.region_id) {
            const regionId = params.region_id;
            delete params.region_id;
            communities = await this.request(`/communities/region/${regionId}`);
        } else {
            const regions = await this.getRegions();
            const allCommunities = [];
            for (const region of regions) {
                try {
                    const regionCommunities = await this.request(`/communities/region/${region.id}`);
                    allCommunities.push(...regionCommunities);
                } catch (err) {
                    console.warn(`Failed to load communities for region ${region.id}:`, err);
                }
            }
            communities = allCommunities;
        }

        // Apply filters
        let filtered = communities;
        if (params.category) {
            filtered = filtered.filter(c => c.category === params.category);
        }
        if (params.is_active !== undefined) {
            filtered = filtered.filter(c => c.is_active === params.is_active);
        }
        if (params.health_status) {
            filtered = filtered.filter(c => (c.health_status || 'active') === params.health_status);
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

    async applySuggestedCategory(id) {
        return this.request(`/communities/${id}/apply-suggested-category`, {
            method: 'POST'
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

