/**
 * PR Guardian AI - Main Application JavaScript
 * Global Alpine.js components and utilities
 */

document.addEventListener('alpine:init', () => {
    // Global error handler for Alpine
    Alpine.onerror((error) => {
        console.error('Alpine error:', error);
    });

    // Global clipboard utility
    Alpine.magic('clipboard', () => ({
        async copy(text) {
            try {
                await navigator.clipboard.writeText(text);
                return true;
            } catch (err) {
                console.error('Failed to copy:', err);
                return false;
            }
        }
    }));

    // Global date formatting utility
    Alpine.magic('formatDate', () => (dateString, format = 'short') => {
        const date = new Date(dateString);

        switch (format) {
            case 'short':
                return date.toLocaleDateString('en-US', {
                    month: 'short',
                    day: 'numeric',
                    year: 'numeric'
                });
            case 'long':
                return date.toLocaleDateString('en-US', {
                    weekday: 'long',
                    year: 'numeric',
                    month: 'long',
                    day: 'numeric'
                });
            case 'relative':
                const seconds = Math.floor((new Date() - date) / 1000);
                if (seconds < 60) return 'just now';
                if (seconds < 3600) return `${Math.floor(seconds / 60)} minutes ago`;
                if (seconds < 86400) return `${Math.floor(seconds / 3600)} hours ago`;
                return `${Math.floor(seconds / 86400)} days ago`;
            default:
                return date.toLocaleDateString();
        }
    });

    // Global notification system
    Alpine.store('notifications', {
        items: [],
        show(message, type = 'info') {
            const id = Date.now();
            this.items.push({ id, message, type });
            setTimeout(() => {
                this.remove(id);
            }, 5000);
        },
        remove(id) {
            this.items = this.items.filter(item => item.id !== id);
        }
    });
});
