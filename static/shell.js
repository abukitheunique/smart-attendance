(async () => {
  let me
  try {
    const r = await fetch('/api/me')
    if (r.status === 401) { location.href = '/login'; return }
    me = await r.json()
  } catch (e) { return }

  const P = {
    grid: '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>',
    check: '<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>',
    users: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
    file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M16 13H8M16 17H8"/>',
    scan: '<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2M7 12h10"/>',
    smile: '<circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2M9 9h.01M15 9h.01"/>',
    download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
    logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
    menu: '<path d="M3 12h18M3 6h18M3 18h18"/>',
    shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>'
  }
  const svg = (k) => '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + P[k] + '</svg>'

  const nav = me.role === 'admin'
    ? [['/admin', 'grid', 'Overview'], ['/admin/applications', 'check', 'Applications'], ['/admin/people', 'users', 'Teachers & Sub-Directors'], ['/admin/reports', 'file', 'Reports']]
    : [['/dashboard', 'grid', 'Dashboard'], ['/', 'scan', 'QR scanner'], ['/face', 'smile', 'Face mode'], ['/api/report.csv', 'download', 'Export CSV']]

  const content = document.createElement('div')
  content.className = 'content'
  Array.from(document.body.childNodes).forEach((n) => { if (n.nodeName !== 'SCRIPT') content.appendChild(n) })

  const sb = document.createElement('aside')
  sb.className = 'sb'

  const toggle = document.createElement('button')
  toggle.innerHTML = svg('menu') + '<span class="label">Collapse</span>'
  toggle.title = 'Collapse or expand the menu'
  sb.appendChild(toggle)

  const brand = document.createElement('div')
  brand.className = 'brand'
  brand.innerHTML = svg('shield') + '<span class="label">Smart <b>Attendance</b></span>'
  sb.appendChild(brand)

  nav.forEach(([href, icon, label]) => {
    const a = document.createElement('a')
    a.href = href
    a.title = label
    a.innerHTML = svg(icon) + '<span class="label">' + label.replace('&', '&amp;') + '</span>'
    if (location.pathname === href) a.className = 'on'
    sb.appendChild(a)
  })

  const grow = document.createElement('div'); grow.className = 'grow'; sb.appendChild(grow)

  const who = document.createElement('div'); who.className = 'me'
  const nm = document.createElement('b'); nm.textContent = me.name
  const rl = document.createElement('span')
  rl.textContent = { admin: 'Director', teacher: 'Teacher', subdirector: 'Sub-Director' }[me.role] || me.role
  who.append(nm, rl); sb.appendChild(who)

  const out = document.createElement('button')
  out.title = 'Log out'
  out.innerHTML = svg('logout') + '<span class="label">Log out</span>'
  out.onclick = async () => { await fetch('/api/logout', { method: 'POST' }); location.href = '/login' }
  sb.appendChild(out)

  let saved = null
  try { saved = localStorage.getItem('sb-collapsed') } catch (e) {}
  const collapsed = saved === null ? window.innerWidth < 800 : saved === '1'
  document.body.classList.add('has-shell')
  if (collapsed) document.body.classList.add('collapsed')
  toggle.onclick = () => {
    const c = document.body.classList.toggle('collapsed')
    try { localStorage.setItem('sb-collapsed', c ? '1' : '0') } catch (e) {}
  }

  document.body.append(sb, content)
})()
