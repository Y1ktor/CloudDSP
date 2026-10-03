import React from 'react';
import { Link, NavLink } from 'react-router-dom';
import AuthPanel from './AuthPanel';

export default function AppNavBar({ authProps }) {
    const navStyle = {
        background: 'var(--studio-surface)', padding: '15px 20px', display: 'flex', gap: '20px',
        borderBottom: '1px solid var(--studio-border)', marginBottom: '40px', alignItems: 'center',
        boxShadow: '0 4px 14px rgba(44, 62, 80, 0.09)', flexWrap: 'wrap',
    };

    return (
        <div className="studio-nav" style={navStyle}>
            <Link to="/" style={{ color: 'var(--studio-text)', fontWeight: '900', fontSize: '20px', marginRight: '20px', letterSpacing: '1px', textDecoration: 'none' }}>CloudDSP</Link>
            <NavLink
                to="/"
                end
                style={({ isActive }) => ({ color: isActive ? 'var(--studio-accent)' : 'var(--studio-text-muted)', fontSize: '13px', fontWeight: '700', textDecoration: 'none' })}
            >Studio</NavLink>
            <NavLink
                to="/architecture"
                style={({ isActive }) => ({ color: isActive ? 'var(--studio-accent)' : 'var(--studio-text-muted)', fontSize: '13px', fontWeight: '700', textDecoration: 'none' })}
            >Architecture</NavLink>
            <NavLink
                to="/k8"
                style={({ isActive }) => ({ color: isActive ? 'var(--studio-accent)' : 'var(--studio-text-muted)', fontSize: '13px', fontWeight: '700', textDecoration: 'none' })}
            >K8</NavLink>
            <NavLink
                to="/cost"
                style={({ isActive }) => ({ color: isActive ? 'var(--studio-accent)' : 'var(--studio-text-muted)', fontSize: '13px', fontWeight: '700', textDecoration: 'none' })}
            >Cost</NavLink>
            <div style={{ marginLeft: 'auto' }}><AuthPanel {...authProps} /></div>
        </div>
    );
}

