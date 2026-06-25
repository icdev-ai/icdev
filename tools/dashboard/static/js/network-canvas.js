/**
 * Network Design Canvas — JointJS canvas logic
 * Manages graph creation, palette drag-drop, object config, save/load.
 */

/* ── State ─────────────────────────────────────────────────────────────────── */
let graph, paper;
let selectedCell = null;
let currentTopoId = TOPOLOGY_ID; // injected by template
let undoStack = [];
let redoStack = [];
let auditResults = {}; // nodeId -> violations[]
let _aiCorrections = []; // {nodeId, ruleId, timestamp, oldValue, newValue}
let saveTimer = null;
let isDirty = false;
let _conflictExportBypassed = false; // ndc-ux-02: bypass conflict gate for "Export Anyway"
let _selectedCells = [];             // ndc-ux-multi: shift+click multi-select list

/* ── CR Markup Mode State ─────────────────────────────────────────────────── */
let _crModeActive = false;
let _crActiveId = null;       // current CR id
let _crActiveStatus = null;   // current CR status
let _crAction = null;         // 'add' | 'remove' | 'modify' | null
let _crItems = [];            // loaded markup items
let _crOverlays = {};         // entity_id -> action_type (for visual overlay)
let _crJustTarget = null;     // {cell, entityId, entityType, entityLabel, data}

/* ── Node type → display config ─────────────────────────────────────────────── */
const NODE_STYLES = {
  // Physical
  'router':           { fill: '#0f2b3a', stroke: '#3498db',  label: 'Router',        symbol: 'R'  },
  'switch-l2':        { fill: '#0f2b0f', stroke: '#27ae60',  label: 'Switch L2',     symbol: 'S2' },
  'switch-l3':        { fill: '#0f2b0f', stroke: '#2ecc71',  label: 'Switch L3',     symbol: 'S3' },
  'firewall':         { fill: '#2b0f0f', stroke: '#e94560',  label: 'Firewall',      symbol: 'FW' },
  'load-balancer':    { fill: '#1a1a0f', stroke: '#f39c12',  label: 'Load Balancer', symbol: 'LB' },
  'wap':              { fill: '#1a0f2b', stroke: '#9b59b6',  label: 'WAP',           symbol: 'AP' },
  'server':           { fill: '#0f2b2b', stroke: '#1abc9c',  label: 'Server',        symbol: 'SV' },
  'patch-panel':      { fill: '#1a1a1a', stroke: '#888',     label: 'Patch Panel',   symbol: 'PP' },
  // Physical Media
  'media-ge':         { fill: '#0f2b1a', stroke: '#48c774',  label: 'GbE Port',      symbol: 'GE' },
  'media-10ge':       { fill: '#0f2b1a', stroke: '#2ecc71',  label: '10GbE',         symbol: '10G'},
  'media-25ge':       { fill: '#0f2b1a', stroke: '#00d2a0',  label: '25GbE',         symbol: '25G'},
  'media-40ge':       { fill: '#0f2b2b', stroke: '#00b8d4',  label: '40GbE',         symbol: '40G'},
  'media-100ge':      { fill: '#0f2b2b', stroke: '#0097a7',  label: '100GbE',        symbol:'100G'},
  'media-400ge':      { fill: '#0f2b3a', stroke: '#006064',  label: '400GbE',        symbol:'400G'},
  'sfp':              { fill: '#1a1a0f', stroke: '#c0ca33',  label: 'SFP',           symbol: 'SFP'},
  'sfp-plus':         { fill: '#1a1a0f', stroke: '#9e9d24',  label: 'SFP+',          symbol: 'SF+'},
  'qsfp':             { fill: '#1a1a0f', stroke: '#827717',  label: 'QSFP28',        symbol: 'QSF'},
  'qsfp-dd':          { fill: '#1a1a0f', stroke: '#6d4c00',  label: 'QSFP-DD',       symbol: 'QDD'},
  'media-fiber':      { fill: '#1a0f2b', stroke: '#b07ce8',  label: 'Fiber',         symbol: 'FO' },
  'media-optical':    { fill: '#0f1a2b', stroke: '#00bfff',  label: 'Optical/DWDM',  symbol: 'OPT'},
  'media-converter':  { fill: '#1a1a1a', stroke: '#aaa',     label: 'Media Conv.',   symbol: 'MC' },
  // Encryption / FIPS 140
  'fips-140-l1':      { fill: '#2b2b0f', stroke: '#d4ac0d',  label: 'FIPS 140 L1',   symbol: 'F1' },
  'fips-140-l2':      { fill: '#2b2b0f', stroke: '#f4d03f',  label: 'FIPS 140 L2',   symbol: 'F2' },
  'fips-140-l3':      { fill: '#2b1a0f', stroke: '#f0b27a',  label: 'FIPS 140 L3',   symbol: 'F3' },
  'fips-140-l4':      { fill: '#2b0f0f', stroke: '#e74c3c',  label: 'FIPS 140 L4',   symbol: 'F4' },
  'hsm':              { fill: '#2b0f1a', stroke: '#ff6b81',  label: 'HSM',           symbol: 'HSM'},
  'type1-encryptor':  { fill: '#2b0f0f', stroke: '#ff4757',  label: 'Type 1 (NSA)',  symbol: 'T1' },
  'kg-175d':          { fill: '#2b0f0f', stroke: '#ff6b6b',  label: 'KG-175D',      symbol: '175D'},
  'kg-175g':          { fill: '#2b0f0f', stroke: '#ff5252',  label: 'KG-175G',      symbol: '175G'},
  'kg-250':           { fill: '#2b0f0f', stroke: '#d32f2f',  label: 'KG-250',       symbol: '250' },
  'kg-340':           { fill: '#2b0f0f', stroke: '#c62828',  label: 'KG-340',       symbol: '340' },
  'kg-245x':          { fill: '#2b0f0f', stroke: '#e57373',  label: 'KG-245X',      symbol: '245X'},
  'kg-255':           { fill: '#2b0f0f', stroke: '#b71c1c',  label: 'KG-255',       symbol: '255' },
  'macsec':           { fill: '#2b1a0f', stroke: '#e67e22',  label: 'MACsec',        symbol: 'MAC'},
  'qkd-device':       { fill: '#0f0f2b', stroke: '#a29bfe',  label: 'QKD Device',    symbol: 'QKD'},
  'tls-terminator':   { fill: '#2b2b0f', stroke: '#fdcb6e',  label: 'TLS Offload',   symbol: 'TLS'},
  // Endpoints
  'endpoint-pc':      { fill: '#0f1a1a', stroke: '#74b9ff',  label: 'Workstation',   symbol: 'PC' },
  'endpoint-phone':   { fill: '#0f1a1a', stroke: '#55efc4',  label: 'IP Phone',      symbol: 'PH' },
  'endpoint-iot':     { fill: '#0f1a1a', stroke: '#fd79a8',  label: 'IoT Device',    symbol: 'IoT'},
  'endpoint-camera':  { fill: '#0f1a1a', stroke: '#e17055',  label: 'IP Camera',     symbol: 'CAM'},
  // Controllers / Monitoring
  'wlc':              { fill: '#1a0f2b', stroke: '#a29bfe',  label: 'WLC',           symbol: 'WLC'},
  'sdwan-edge':       { fill: '#0f2b2b', stroke: '#00cec9',  label: 'SD-WAN Edge',   symbol: 'SDW'},
  'network-tap':      { fill: '#1a1a1a', stroke: '#636e72',  label: 'Network TAP',   symbol: 'TAP'},
  'siem':             { fill: '#1a0f1a', stroke: '#6c5ce7',  label: 'SIEM',          symbol: 'SIM'},
  // Optical / DWDM
  'roadm':            { fill: '#1a0f2b', stroke: '#9b59b6',  label: 'ROADM',         symbol: 'RDM'},
  'oadm':             { fill: '#1a0f2b', stroke: '#8e44ad',  label: 'OADM',          symbol: 'OAD'},
  'edfa':             { fill: '#0f1a2b', stroke: '#00bfff',  label: 'EDFA Amp',      symbol: 'AMP'},
  'transponder':      { fill: '#0f0f2b', stroke: '#7c4dff',  label: 'Transponder',   symbol: 'TXP'},
  'olt':              { fill: '#0f1a2b', stroke: '#4fc3f7',  label: 'OLT',           symbol: 'OLT'},
  'odf':              { fill: '#1a1a1a', stroke: '#90a4ae',  label: 'ODF',           symbol: 'ODF'},
  // Carrier / SP
  'pop':              { fill: '#0f2b1a', stroke: '#66bb6a',  label: 'POP',           symbol: 'POP'},
  'mpls-pe':          { fill: '#2b1a0f', stroke: '#ff9800',  label: 'MPLS PE',       symbol: 'PE' },
  'mpls-p':           { fill: '#2b1a0f', stroke: '#ffb74d',  label: 'MPLS P',        symbol: 'P'  },
  'route-reflector':  { fill: '#1a1a2e', stroke: '#7986cb',  label: 'Route Reflector',symbol: 'RR'},
  'sonet-adm':        { fill: '#0f0f2b', stroke: '#536dfe',  label: 'SONET ADM',     symbol: 'ADM'},
  'patch-panel-fiber':{ fill: '#1a1a1a', stroke: '#b07ce8',  label: 'Fiber Patch',   symbol: 'FPP'},
  // Annotation
  'annotation':       { fill: '#1a1a2e', stroke: '#636e72',  label: 'Note',          symbol: '...' },
  // Group container
  'group-site':       { fill: 'rgba(15,25,60,0.4)', stroke: '#5f6e8e', label: 'Site Group', symbol: '' },
  // Drawing shapes
  'draw-rect':        { fill: '#1a2a3a', stroke: '#4a9eff',  label: 'Rectangle',     symbol: '',  shape: 'rect' },
  'draw-rounded-rect':{ fill: '#1a2a3a', stroke: '#4a9eff',  label: 'Rounded Rect',  symbol: '',  shape: 'roundedrect' },
  'draw-circle':      { fill: '#1a2a3a', stroke: '#e94560',  label: 'Circle',        symbol: '',  shape: 'circle' },
  'draw-ellipse':     { fill: '#1a2a3a', stroke: '#9b59b6',  label: 'Ellipse',       symbol: '',  shape: 'ellipse' },
  'draw-diamond':     { fill: '#1a2a3a', stroke: '#f39c12',  label: 'Diamond',       symbol: '',  shape: 'diamond' },
  'draw-triangle':    { fill: '#1a2a3a', stroke: '#27ae60',  label: 'Triangle',      symbol: '',  shape: 'triangle' },
  'draw-hexagon':     { fill: '#1a2a3a', stroke: '#e67e22',  label: 'Hexagon',       symbol: '',  shape: 'hexagon' },
  'draw-star':        { fill: '#2b2b0f', stroke: '#f1c40f',  label: 'Star',          symbol: '',  shape: 'star' },
  'draw-line-h':      { fill: 'transparent', stroke: '#7a8cb0', label: 'H-Line',     symbol: '',  shape: 'hline' },
  'draw-line-v':      { fill: 'transparent', stroke: '#7a8cb0', label: 'V-Line',     symbol: '',  shape: 'vline' },
  'draw-arrow':       { fill: 'transparent', stroke: '#e94560', label: 'Arrow',      symbol: '',  shape: 'arrow' },
  // Text label
  'text-label':       { fill: 'transparent', stroke: 'transparent', label: 'Text',   symbol: '',  shape: 'text' },
  'text-heading':     { fill: 'transparent', stroke: 'transparent', label: 'Heading', symbol: '',  shape: 'heading' },
  'text-badge':       { fill: '#0f3460',     stroke: '#4a9eff',    label: 'Badge',   symbol: '',  shape: 'badge' },
  'callout-bubble':   { fill: '#e94560',     stroke: '#e94560',    label: '1',       symbol: '1', shape: 'circle' },
  // Logical
  'vrf':              { fill: '#0f1a2b', stroke: '#5dade2',  label: 'VRF',           symbol: 'VRF'},
  'vlan':             { fill: '#0f2b0f', stroke: '#58d68d',  label: 'VLAN',          symbol: 'VL' },
  'subnet':           { fill: '#1a0f1a', stroke: '#c39bd3',  label: 'Subnet',        symbol: '⊂' },
  'security-zone':    { fill: '#2b1a0f', stroke: '#e59866',  label: 'Sec Zone',      symbol: 'Z'  },
  'gre-tunnel':       { fill: '#1a1a0f', stroke: '#f9e79f',  label: 'GRE Tunnel',    symbol: 'GRE'},
  'ipsec-tunnel':     { fill: '#1a1a0f', stroke: '#f7dc6f',  label: 'IPSec Tunnel',  symbol: 'IPS'},
  'mpls-lsp':         { fill: '#0f1a2b', stroke: '#85c1e9',  label: 'MPLS LSP',      symbol: 'LSP'},
  // Cloud
  // AWS (orange family)
  'aws-vpc':          { fill: '#2b1a0f', stroke: '#ff9900', label: 'VPC',           symbol: 'VPC'},
  'aws-subnet':       { fill: '#2b1a0f', stroke: '#ffb84d', label: 'Subnet',        symbol: 'SUB'},
  'aws-tgw':          { fill: '#2b1a0f', stroke: '#ff9900', label: 'Transit GW',    symbol: 'TGW'},
  'aws-dx':           { fill: '#2b1a0f', stroke: '#f0a500', label: 'Direct Connect', symbol: 'DX'},
  'aws-vpn':          { fill: '#2b1a0f', stroke: '#ffb84d', label: 'Site VPN',      symbol: 'VPN'},
  'aws-alb':          { fill: '#2b1a0f', stroke: '#ff9900', label: 'ALB',           symbol: 'ALB'},
  'aws-nlb':          { fill: '#2b1a0f', stroke: '#ff9900', label: 'NLB',           symbol: 'NLB'},
  'aws-cloudfront':   { fill: '#2b1a0f', stroke: '#ffb84d', label: 'CloudFront',    symbol: 'CF'},
  'aws-r53':          { fill: '#2b1a0f', stroke: '#ff9900', label: 'Route 53',      symbol: 'R53'},
  'aws-nfw':          { fill: '#2b0f0f', stroke: '#ff9900', label: 'Network FW',    symbol: 'NFW'},
  'aws-waf':          { fill: '#2b1a0f', stroke: '#ffb84d', label: 'WAF',           symbol: 'WAF'},
  'aws-gw-ep':        { fill: '#2b1a0f', stroke: '#ff9900', label: 'GW Endpoint',   symbol: 'GEP'},
  'aws-direct-connect': { fill: '#2b1a0f', stroke: '#f0a500', label: 'Direct Connect', symbol: 'DX'},
  // Azure (blue family)
  'az-vnet':          { fill: '#0f0f2b', stroke: '#0078d4', label: 'VNet',          symbol: 'VNT'},
  'az-subnet':        { fill: '#0f0f2b', stroke: '#5b9bd5', label: 'Subnet',        symbol: 'SUB'},
  'az-vwan':          { fill: '#0f0f2b', stroke: '#0078d4', label: 'Virtual WAN',   symbol: 'WAN'},
  'az-er':            { fill: '#0f0f2b', stroke: '#5b9bd5', label: 'ExpressRoute',  symbol: 'ER'},
  'az-vpn-gw':        { fill: '#0f0f2b', stroke: '#0078d4', label: 'VPN Gateway',   symbol: 'VGW'},
  'az-fw':            { fill: '#0f0f2b', stroke: '#0078d4', label: 'Azure Firewall', symbol: 'AFW'},
  'az-appgw':         { fill: '#0f0f2b', stroke: '#5b9bd5', label: 'App Gateway',   symbol: 'AGW'},
  'az-front':         { fill: '#0f0f2b', stroke: '#0078d4', label: 'Front Door',    symbol: 'FD'},
  'az-dns':           { fill: '#0f0f2b', stroke: '#5b9bd5', label: 'Azure DNS',     symbol: 'DNS'},
  'az-bastion':       { fill: '#0f0f2b', stroke: '#0078d4', label: 'Bastion',       symbol: 'BST'},
  'az-nsg':           { fill: '#0f0f2b', stroke: '#5b9bd5', label: 'NSG',           symbol: 'NSG'},
  'azure-expressroute': { fill: '#0f0f2b', stroke: '#5b9bd5', label: 'ExpressRoute', symbol: 'ER'},
  // GCP (blue/green family)
  'gcp-vpc':          { fill: '#0f1a2b', stroke: '#4285f4', label: 'VPC',           symbol: 'VPC'},
  'gcp-subnet':       { fill: '#0f1a2b', stroke: '#4285f4', label: 'Subnet',        symbol: 'SUB'},
  'gcp-ic':           { fill: '#0f1a2b', stroke: '#4285f4', label: 'Interconnect',  symbol: 'IC'},
  'gcp-vpn':          { fill: '#0f1a2b', stroke: '#34a853', label: 'Cloud VPN',     symbol: 'VPN'},
  'gcp-nat':          { fill: '#0f1a2b', stroke: '#34a853', label: 'Cloud NAT',     symbol: 'NAT'},
  'gcp-lb':           { fill: '#0f1a2b', stroke: '#4285f4', label: 'Cloud LB',      symbol: 'CLB'},
  'gcp-armor':        { fill: '#0f1a2b', stroke: '#ea4335', label: 'Cloud Armor',   symbol: 'ARM'},
  'gcp-cdn':          { fill: '#0f1a2b', stroke: '#4285f4', label: 'Cloud CDN',     symbol: 'CDN'},
  'gcp-dns':          { fill: '#0f1a2b', stroke: '#34a853', label: 'Cloud DNS',     symbol: 'DNS'},
  'gcp-router':       { fill: '#0f1a2b', stroke: '#4285f4', label: 'Cloud Router',  symbol: 'CR'},
  'gcp-interconnect': { fill: '#0f2b0f', stroke: '#34a853', label: 'Interconnect',  symbol: 'IC'},
  // OCI (red family)
  'oci-vcn':          { fill: '#2b0f0f', stroke: '#f80000', label: 'VCN',           symbol: 'VCN'},
  'oci-subnet':       { fill: '#2b0f0f', stroke: '#ff4444', label: 'Subnet',        symbol: 'SUB'},
  'oci-drg':          { fill: '#2b0f0f', stroke: '#f80000', label: 'DRG',           symbol: 'DRG'},
  'oci-fc':           { fill: '#2b0f0f', stroke: '#f80000', label: 'FastConnect',   symbol: 'FC'},
  'oci-lb':           { fill: '#2b0f0f', stroke: '#ff4444', label: 'Load Balancer', symbol: 'OLB'},
  'oci-waf':          { fill: '#2b0f0f', stroke: '#f80000', label: 'WAF',           symbol: 'WAF'},
  'oci-nsg':          { fill: '#2b0f0f', stroke: '#ff4444', label: 'NSG',           symbol: 'NSG'},
  'oci-fastconnect':  { fill: '#2b0f0f', stroke: '#f80000', label: 'FastConnect',   symbol: 'FC'},
  // IBM (blue family)
  'ibm-vpc':          { fill: '#0f0f2b', stroke: '#0f62fe', label: 'VPC',           symbol: 'VPC'},
  'ibm-subnet':       { fill: '#0f0f2b', stroke: '#4589ff', label: 'Subnet',        symbol: 'SUB'},
  'ibm-dl':           { fill: '#0f0f2b', stroke: '#0f62fe', label: 'Direct Link',   symbol: 'DL'},
  'ibm-vpn':          { fill: '#0f0f2b', stroke: '#4589ff', label: 'VPN Gateway',   symbol: 'VPN'},
  'ibm-lb':           { fill: '#0f0f2b', stroke: '#0f62fe', label: 'Load Balancer', symbol: 'ILB'},
  'ibm-tg':           { fill: '#0f0f2b', stroke: '#0f62fe', label: 'Transit GW',    symbol: 'TG'},
  // Colocation / Cross-Connect
  'meet-me-room':     { fill: '#1a0f2b', stroke: '#a29bfe', label: 'Meet-Me Room',  symbol: 'MMR'},
  'cross-connect':    { fill: '#1a1a0f', stroke: '#fdcb6e', label: 'Cross-Connect', symbol: 'XX' },
  // Multi-cloud
  'cloud-peering':    { fill: '#1a0f2b', stroke: '#8b5cf6', label: 'Cloud Peering', symbol: 'PER'},
  'sdwan-overlay':    { fill: '#0f2b2b', stroke: '#00cec9', label: 'SD-WAN',        symbol: 'SDW'},
  'sase-pop':         { fill: '#1a0f2b', stroke: '#a29bfe', label: 'SASE PoP',      symbol: 'SSE'},
  'internet-exchange':{ fill: '#0f2b1a', stroke: '#66bb6a', label: 'IXP',           symbol: 'IXP'},
  'cloud-region':     { fill: '#1a1a2e', stroke: '#636e72', label: 'Region',        symbol: 'REG'},
  'cloud':            { fill: '#0f0f2b', stroke: '#7f8c8d',  label: 'Cloud',         symbol: '☁' },
  // ── DoD JWICS (SECRET/TS — red family, classified crimson) ──────────────────
  'dod-jwics-backbone':    { fill: '#2b0808', stroke: '#e74c3c', label: 'JWICS Backbone',     symbol: 'JWX' },
  'dod-jwics-gateway':     { fill: '#2b0808', stroke: '#ff6b6b', label: 'JWICS Gateway',      symbol: 'JWG' },
  'dod-jwics-dns':         { fill: '#2b0a0a', stroke: '#ff4757', label: 'JWICS DNS',           symbol: 'JDN' },
  'dod-jwics-mail-relay':  { fill: '#2b0a0a', stroke: '#e84393', label: 'JWICS Mail Relay',   symbol: 'JMR' },
  'dod-type1-encryptor':   { fill: '#2b0f0f', stroke: '#ff4757', label: 'Type 1 Encryptor',   symbol: 'T1E' },
  'dod-scif-lan':          { fill: '#1a0808', stroke: '#c0392b', label: 'SCIF LAN',            symbol: 'SCF' },
  // ── DoD C2S — AWS Secret Region (amber-red, classified orange) ───────────
  'dod-c2s-direct-connect':{ fill: '#2b1200', stroke: '#e67e22', label: 'C2S ClassifiedConnect', symbol: 'C2D' },
  'dod-c2s-tgw':           { fill: '#2b1200', stroke: '#ff9800', label: 'C2S Transit GW',     symbol: 'C2T' },
  'dod-c2s-vpc':           { fill: '#2b1200', stroke: '#ffb300', label: 'C2S VPC',             symbol: 'C2V' },
  'dod-c2s-dns-phz':       { fill: '#2b1000', stroke: '#ffa000', label: 'C2S Route 53 PHZ',   symbol: 'C2Z' },
  // ── DoD C2E — Azure Government Secret (dark violet, classified purple) ───
  'dod-c2e-expressroute':  { fill: '#120a2b', stroke: '#8e44ad', label: 'C2E ExpressRoute',   symbol: 'C2X' },
  'dod-c2e-vnet':          { fill: '#120a2b', stroke: '#9b59b6', label: 'C2E VNet',            symbol: 'C2N' },
  'dod-c2e-dns-private':   { fill: '#120a2b', stroke: '#a569bd', label: 'C2E Private DNS',    symbol: 'C2P' },
  // ── Shared DISA Secret-side ───────────────────────────────────────────────
  'dod-secret-bcap':       { fill: '#2b0808', stroke: '#ff4757', label: 'Secret BCAP/CAP',    symbol: 'SBC' },
  'dod-cds':               { fill: '#1a0a1a', stroke: '#ff7675', label: 'Cross-Domain (CDS)', symbol: 'CDS' },
  // Aliases for ingester types (so they never show '?')
  'vpn_gateway':      { fill: '#0f2b2b', stroke: '#00bcd4',  label: 'VPN GW',        symbol: '🔒' },
  'wan_link':         { fill: '#0f0f2b', stroke: '#7f8c8d',  label: 'WAN',           symbol: '☁' },
  'load_balancer':    { fill: '#1a1a0f', stroke: '#f39c12',  label: 'Load Balancer', symbol: 'LB' },
  'access_point':     { fill: '#1a0f2b', stroke: '#9b59b6',  label: 'WAP',           symbol: 'AP' },
  'cloud_service':    { fill: '#0f0f2b', stroke: '#29b6f6',  label: 'Cloud Svc',     symbol: '☁' },
  'switch':           { fill: '#0f2b0f', stroke: '#27ae60',  label: 'Switch',        symbol: 'SW' },
  'pdu':              { fill: '#fff3e0', stroke: '#e65100',  label: 'PDU',           symbol: 'PDU' },
  'ups':              { fill: '#fff8e1', stroke: '#f57f17',  label: 'UPS',           symbol: 'UPS' },
  'unknown':          { fill: '#f5f5f5', stroke: '#7a8cb0',  label: 'Device',        symbol: '?' },
  // ── Cisco Physical — solid Cisco blue fill, dark body, white detail ───────
  // fill = node card bg (Cisco blue), stroke = stencil body fill (dark for contrast)
  'cisco-router':     { fill: '#049fd9', stroke: '#004e78', label: 'Cisco Router',    symbol: 'CR'  },
  'cisco-switch-l2':  { fill: '#049fd9', stroke: '#004e78', label: 'Cisco Switch L2', symbol: 'CS2' },
  'cisco-switch-l3':  { fill: '#049fd9', stroke: '#004e78', label: 'Cisco Switch L3', symbol: 'CS3' },
  'cisco-firewall':   { fill: '#c0392b', stroke: '#7b0000', label: 'Cisco Firewall',  symbol: 'CFW' },
  'cisco-lb':         { fill: '#049fd9', stroke: '#004e78', label: 'Cisco LB',        symbol: 'CLB' },
  // ── Juniper Physical — solid Juniper green fill, dark body, white detail ─
  'juniper-ptx10003': { fill: '#84b135', stroke: '#3d5a0f', label: 'Juniper PTX10003',symbol: 'PTX' },
  'juniper-mx304':    { fill: '#84b135', stroke: '#3d5a0f', label: 'Juniper MX304',   symbol: 'MX'  },
};

// Alias map: ingester/intelligence types → canvas NODE_STYLES keys
const _TYPE_ALIASES = {
  'switch': 'switch-l2', 'load_balancer': 'load-balancer', 'access_point': 'wap',
  'vpn_gateway': 'router', 'wan_link': 'cloud', 'cloud_service': 'cloud',
  'unknown': 'server',
};

function getStyle(type) {
  return NODE_STYLES[type] || NODE_STYLES[_TYPE_ALIASES[type]] || { fill: '#1a1a2e', stroke: '#7a8cb0', label: type, symbol: '?' };
}

/* ── Cisco Traditional Stencils — filled SVG shapes (48x48 viewBox) ────────── */
// Each entry: { body: filled shape path, detail: white internal strokes }
// Authentic Cisco stencil shapes (48x48 viewBox)
// Reference: cisco.com/c/en/us/products/visio-stencil-listing.html
const CISCO_STENCILS = {
  // ── Router: circle + 4 cardinal arrows (N/S/E/W) ──
  'router': {
    body: 'M24,6 a18,18 0 1,0 0.01,0 Z',
    detail: 'M24,6 v8 M20,10 l4,-4 l4,4 M24,42 v-8 M20,38 l4,4 l4,-4 M6,24 h8 M10,20 l-4,4 l4,4 M42,24 h-8 M38,20 l4,4 l-4,4 M24,18 v12 M18,24 h12',
  },
  // ── Switch L2: rectangle + 2 paired arrows each side ──
  'switch-l2': {
    body: 'M10,16 h28 v16 h-28 Z',
    detail: 'M10,21 l-6,-3 v6 Z M10,30 l-6,-3 v6 Z M38,21 l6,-3 v6 Z M38,30 l6,-3 v6 Z M16,22 h16 M16,26 h16',
  },
  // ── Switch L3: switch rect + circle overlay on top (multilayer) ──
  'switch-l3': {
    body: 'M10,20 h28 v16 h-28 Z M24,8 a10,10 0 1,0 0.01,0 Z',
    detail: 'M10,25 l-6,-3 v6 Z M10,34 l-6,-3 v6 Z M38,25 l6,-3 v6 Z M38,34 l6,-3 v6 Z M16,26 h16 M16,30 h16 M20,8 l4,-4 l4,4 M24,4 v6',
  },
  // ── Firewall: upright brick wall (portrait rectangle) ──
  'firewall': {
    body: 'M12,6 h24 v36 h-24 Z',
    detail: 'M12,12 h24 M12,18 h24 M12,24 h24 M12,30 h24 M12,36 h24 M24,6 v6 M20,12 v6 M28,12 v6 M24,18 v6 M20,24 v6 M28,24 v6 M24,30 v6 M20,36 v6 M28,36 v6',
  },
  // ── Server: tower/rack with drive bays ──
  'server': {
    body: 'M14,6 h20 v36 h-20 Z',
    detail: 'M14,14 h20 M14,22 h20 M14,30 h20 M28,9 a1.5,1.5 0 1,0 0.01,0 M28,17 a1.5,1.5 0 1,0 0.01,0 M28,25 a1.5,1.5 0 1,0 0.01,0 M28,33 a1.5,1.5 0 1,0 0.01,0',
  },
  // ── Load Balancer: right-pointing arrow/chevron ──
  'load-balancer': {
    body: 'M8,12 h18 l14,12 l-14,12 h-18 Z',
    detail: 'M14,20 h10 M14,24 h10 M14,28 h10',
  },
  // ── WAP: dot with concentric radio arcs ──
  'wap': {
    body: 'M22,38 h4 v4 h-4 Z M20,38 l4,6 l4,-6',
    detail: 'M24,38 v-14 M24,24 a0,0 0 1,0 0,0 M19,21 a7,7 0 0,1 10,0 M15,18 a12,12 0 0,1 18,0 M11,15 a17,17 0 0,1 26,0 M7,12 a22,22 0 0,1 34,0',
  },
  // ── Cloud: classic bumpy cloud ──
  'cloud': {
    body: 'M14,34 C6,34 2,28 6,22 C4,14 12,10 20,12 C22,6 32,6 36,12 C40,8 46,14 44,22 C48,26 44,34 38,34 Z',
    detail: '',
  },
  // ── Patch Panel ──
  'patch-panel': {
    body: 'M6,18 h36 v12 h-36 Z',
    detail: 'M12,21 v6 M18,21 v6 M24,21 v6 M30,21 v6 M36,21 v6',
  },
  // ── Meet-Me Room: building with roof ──
  'meet-me-room': {
    body: 'M8,40 V18 L24,6 L40,18 V40 Z',
    detail: 'M18,40 V28 h12 V40 M14,22 h4 v3 h-4 Z M30,22 h4 v3 h-4 Z',
  },
  // ── Cross-Connect: patch panel with X fiber runs ──
  'cross-connect': {
    body: 'M6,16 h36 v16 h-36 Z',
    detail: 'M14,16 v16 M24,16 v16 M34,16 v16 M10,19 l6,10 M16,19 l-6,10 M28,19 l6,10 M34,19 l-6,10',
  },
  // ── SIEM: monitor with magnifier ──
  'siem': {
    body: 'M8,10 h32 v22 h-32 Z M18,36 h12 M22,32 v4 M26,32 v4 M16,40 h16',
    detail: 'M22,21 a5,5 0 1,0 0.01,0 M27,26 l4,4',
  },
  // ── ROADM: hexagon ──
  'roadm': {
    body: 'M16,10 h16 l8,14 l-8,14 h-16 l-8,-14 Z',
    detail: 'M20,24 h8 M24,20 v8',
  },
  // ── Transponder: diamond ──
  'transponder': {
    body: 'M24,8 l16,16 l-16,16 l-16,-16 Z',
    detail: 'M20,24 h8 M24,20 v8',
  },
  // ── Workstation/PC: monitor + stand ──
  'endpoint-pc': {
    body: 'M8,8 h32 v22 h-32 Z',
    detail: 'M12,12 h24 v14 h-24 Z M22,30 v4 M26,30 v4 M18,34 h12 v2 h-12 Z',
  },
  // ── IP Phone: handset on cradle ──
  'endpoint-phone': {
    body: 'M14,10 h20 v28 h-20 Z',
    detail: 'M18,14 h12 v6 h-12 Z M18,24 h4 v3 h-4 Z M26,24 h4 v3 h-4 Z M18,30 h4 v3 h-4 Z M26,30 h4 v3 h-4 Z',
  },
  // ── IoT: sensor chip with pins ──
  'endpoint-iot': {
    body: 'M16,16 h16 v16 h-16 Z',
    detail: 'M20,16 v-8 M24,16 v-8 M28,16 v-8 M20,32 v8 M24,32 v8 M28,32 v8 M16,20 h-8 M16,24 h-8 M16,28 h-8 M32,20 h8 M32,24 h8 M32,28 h8',
  },
  // ── Camera: body + lens ──
  'endpoint-camera': {
    body: 'M10,18 h28 v16 h-28 Z',
    detail: 'M24,26 a4,4 0 1,0 0.01,0 M32,16 l4,-4 v4',
  },
  // ── SD-WAN Edge: oval hub ──
  'sdwan-edge': {
    body: 'M24,14 a16,10 0 1,0 0.01,0 Z',
    detail: 'M16,22 h16 M16,26 h16',
  },
  // ── POP: building ──
  'pop': {
    body: 'M10,40 V18 L24,8 L38,18 V40 Z',
    detail: 'M10,18 L24,8 L38,18 M18,24 h12 v12 h-12 Z',
  },
};

// Map device type to stencil (with fallback matching)
function getCiscoStencil(type) {
  // Infrastructure types use text symbols, not stencils
  if (type === 'pdu' || type === 'ups') return null;
  if (CISCO_STENCILS[type]) return CISCO_STENCILS[type];
  if (type.startsWith('cisco-switch-l3') || type.startsWith('switch-l3')) return CISCO_STENCILS['switch-l3'];
  if (type.startsWith('cisco-switch') || type.startsWith('switch')) return CISCO_STENCILS['switch-l2'];
  if (type.includes('router') || type === 'mpls-pe' || type === 'mpls-p' || type === 'route-reflector') return CISCO_STENCILS['router'];
  if (type.startsWith('juniper-')) return CISCO_STENCILS['router'];
  if (type.includes('firewall') || type.includes('fw') || type.includes('nfw')) return CISCO_STENCILS['firewall'];
  if (type.includes('server') || type.includes('srv') || type.includes('historian')) return CISCO_STENCILS['server'];
  if (type.includes('vpn') || type === 'vpn_gateway') return CISCO_STENCILS['router'];
  if (type.includes('balancer') || type === 'load_balancer' || type.includes('lb') || type.includes('alb') || type.includes('nlb')) return CISCO_STENCILS['load-balancer'];
  if (type.includes('cloud') || type === 'wan_link' || type === 'cloud_service' || type.includes('vpc') || type.includes('vnet') || type.includes('vcn')) return CISCO_STENCILS['cloud'];
  if (type.includes('wap') || type === 'wlc' || type === 'access_point') return CISCO_STENCILS['wap'];
  if (type === 'switch') return CISCO_STENCILS['switch-l2'];
  if (type.includes('panel') || type.includes('odf')) return CISCO_STENCILS['patch-panel'];
  if (type.includes('roadm') || type.includes('oadm')) return CISCO_STENCILS['roadm'];
  if (type.includes('transponder') || type.includes('edfa')) return CISCO_STENCILS['transponder'];
  if (type === 'meet-me-room') return CISCO_STENCILS['meet-me-room'];
  if (type === 'cross-connect') return CISCO_STENCILS['cross-connect'];
  if (type === 'siem') return CISCO_STENCILS['siem'];
  if (type === 'sdwan-edge' || type === 'sase-pop') return CISCO_STENCILS['sdwan-edge'];
  if (type === 'pop') return CISCO_STENCILS['pop'];
  if (type.includes('endpoint-pc') || type.includes('workstation')) return CISCO_STENCILS['endpoint-pc'];
  if (type.includes('endpoint-phone')) return CISCO_STENCILS['endpoint-phone'];
  if (type.includes('endpoint-iot') || type.includes('plc') || type.includes('rtu')) return CISCO_STENCILS['endpoint-iot'];
  if (type.includes('endpoint-camera') || type.includes('camera')) return CISCO_STENCILS['endpoint-camera'];
  // CSP transit / interconnect / gateway → router stencil
  if (type.includes('tgw') || type === 'aws-dx' || type === 'az-er' || type === 'azure-expressroute' ||
      type === 'gcp-ic' || type === 'gcp-interconnect' || type === 'gcp-router' ||
      type === 'oci-drg' || type === 'ibm-dl' || type === 'ibm-tg' ||
      type === 'aws-r53' || type === 'gcp-dns' || type === 'az-dns') return CISCO_STENCILS['router'];
  // CSP managed firewall / security → firewall stencil
  if (type === 'aws-nfw' || type === 'aws-waf' || type === 'az-fw' || type === 'az-nsg' ||
      type === 'gcp-armor' || type === 'oci-waf' || type === 'oci-nsg') return CISCO_STENCILS['firewall'];
  // CSP load balancers → load-balancer stencil
  if (type === 'aws-alb' || type === 'aws-nlb' || type === 'gcp-lb' || type === 'oci-lb' ||
      type === 'ibm-lb' || type === 'az-appgw' || type === 'az-front') return CISCO_STENCILS['load-balancer'];
  // CSP VPN gateways → router stencil
  if (type === 'aws-vpn' || type === 'az-vpn-gw' || type === 'gcp-vpn' || type === 'ibm-vpn') return CISCO_STENCILS['router'];
  // CSP VPC/VNet/VCN → cloud stencil
  if (type === 'aws-vpc' || type === 'az-vnet' || type === 'gcp-vpc' || type === 'oci-vcn' ||
      type === 'ibm-vpc' || type === 'dod-c2s-vpc' || type === 'dod-c2e-vnet' || type === 'cloud-region') return CISCO_STENCILS['cloud'];
  return null;
}

// Legacy compatibility
const CISCO_ICONS = {};
function getCiscoPath(type) { return null; }

// Map device categories to icon paths (fallback to generic rectangle)
function getCiscoPath(type) {
  if (CISCO_ICONS[type]) return CISCO_ICONS[type];
  // Category matching
  if (type.startsWith('switch')) return CISCO_ICONS['switch-l2'];
  if (type.includes('router') || type.includes('pe') || type.includes('rr')) return CISCO_ICONS['router'];
  if (type.includes('firewall') || type.includes('fw') || type.includes('nfw')) return CISCO_ICONS['firewall'];
  if (type.includes('server') || type.includes('srv')) return CISCO_ICONS['server'];
  if (type.includes('balancer') || type.includes('lb') || type.includes('alb') || type.includes('nlb')) return CISCO_ICONS['load-balancer'];
  if (type.includes('cloud') || type.includes('vpc') || type.includes('vnet') || type.includes('vcn')) return CISCO_ICONS['cloud'];
  if (type.includes('wap') || type.includes('ap') || type.includes('bastion')) return CISCO_ICONS['wap'];
  if (type.includes('panel') || type.includes('odf')) return CISCO_ICONS['patch-panel'];
  if (type.includes('roadm') || type.includes('oadm')) return CISCO_ICONS['roadm'];
  if (type.includes('transponder') || type.includes('edfa')) return CISCO_ICONS['transponder'];
  if (type === 'meet-me-room' || type.includes('mmr')) return CISCO_ICONS['meet-me-room'];
  if (type === 'cross-connect' || type.includes('xconn')) return CISCO_ICONS['cross-connect'];
  return null; // fallback to rect
}

/* ── JointJS custom shape — Cisco Traditional Stencil ──────────────────────── */
const NetworkNode = joint.dia.Element.define('network.Node', {
  attrs: {
    body: {
      refWidth: '100%', refHeight: '100%',
      fill: 'transparent', stroke: 'transparent', strokeWidth: 0,
      magnet: true,
      cursor: 'pointer',
    },
    stencilGroup: {
      transform: 'translate(19, 3) scale(1.5)',  // Scale 48→72px centered in 110w
    },
    stencilBody: {
      d: '',
      fill: '#1a7abf',  // Classic Cisco blue
      stroke: 'none',
      strokeWidth: 0,
    },
    stencilDetail: {
      d: '',
      fill: 'none',
      stroke: '#ffffff',
      strokeWidth: 1.5,
      strokeLinecap: 'round',
      strokeLinejoin: 'round',
    },
    symbol: {
      refX: '50%', refY: 14,
      textAnchor: 'middle',
      fontSize: 11,
      fontWeight: 'bold',
      fontFamily: 'Cascadia Code, Consolas, monospace',
    },
    label: {
      refX: '50%', refY: -2,
      textAnchor: 'middle',
      fontSize: 9,
      fontWeight: '700',
      fontFamily: 'Segoe UI, system-ui, sans-serif',
      fill: '#1a1a2e',
    },
    sublabel: {
      display: 'none',
      text: '',
    },
    iplabel: {
      refX: '50%', refY: '100%',
      textAnchor: 'middle',
      dy: 4,
      fontSize: 8,
      fontFamily: 'Cascadia Code, Consolas, monospace',
      fill: '#1565c0',
      text: '',
    }
  },
  ports: {
    groups: {
      'connection': {
        position: { name: 'ellipseSpread', args: { dr: 0, step: 10, startAngle: 0 } },
        attrs: {
          portBody: {
            magnet: 'active',
            r: 5,
            fill: '#e94560',
            stroke: '#e94560',
            strokeWidth: 1,
            opacity: 0,
          }
        },
        markup: [{ tagName: 'circle', selector: 'portBody' }],
      }
    },
    items: [
      { group: 'connection', id: 'top',    args: { x: '50%', y: 0 } },
      { group: 'connection', id: 'right',  args: { x: '100%', y: '50%' } },
      { group: 'connection', id: 'bottom', args: { x: '50%', y: '100%' } },
      { group: 'connection', id: 'left',   args: { x: 0, y: '50%' } },
    ],
  },
}, {
  markup: [
    { tagName: 'rect', selector: 'body' },
    { tagName: 'g', selector: 'stencilGroup', children: [
      { tagName: 'path', selector: 'stencilBody' },
      { tagName: 'path', selector: 'stencilDetail' },
    ]},
    { tagName: 'text', selector: 'symbol' },
    { tagName: 'text', selector: 'label' },
    { tagName: 'text', selector: 'sublabel' },
    { tagName: 'text', selector: 'iplabel' },
  ]
});

/* ── Drawing Shape — SVG path generators ───────────────────────────────────── */
function drawingShapePath(shape, w, h) {
  switch (shape) {
    case 'rect':
      return `M0,0 H${w} V${h} H0 Z`;
    case 'roundedrect':
      const r = Math.min(12, w/4, h/4);
      return `M${r},0 H${w-r} Q${w},0 ${w},${r} V${h-r} Q${w},${h} ${w-r},${h} H${r} Q0,${h} 0,${h-r} V${r} Q0,0 ${r},0 Z`;
    case 'circle': {
      const cx = w/2, cy = h/2, rx = w/2, ry = h/2;
      return `M${cx-rx},${cy} A${rx},${ry} 0 1,1 ${cx+rx},${cy} A${rx},${ry} 0 1,1 ${cx-rx},${cy} Z`;
    }
    case 'ellipse': {
      const cx2 = w/2, cy2 = h/2, rx2 = w/2, ry2 = h/2;
      return `M${cx2-rx2},${cy2} A${rx2},${ry2} 0 1,1 ${cx2+rx2},${cy2} A${rx2},${ry2} 0 1,1 ${cx2-rx2},${cy2} Z`;
    }
    case 'diamond':
      return `M${w/2},0 L${w},${h/2} L${w/2},${h} L0,${h/2} Z`;
    case 'triangle':
      return `M${w/2},0 L${w},${h} L0,${h} Z`;
    case 'hexagon': {
      const hx = w * 0.25;
      return `M${hx},0 H${w-hx} L${w},${h/2} L${w-hx},${h} H${hx} L0,${h/2} Z`;
    }
    case 'star': {
      const cx3 = w/2, cy3 = h/2, or3 = Math.min(w,h)/2, ir = or3 * 0.4;
      let d = '';
      for (let i = 0; i < 10; i++) {
        const angle = (Math.PI/2) + (i * Math.PI/5);
        const rad = i % 2 === 0 ? or3 : ir;
        const px = cx3 + rad * Math.cos(angle);
        const py = cy3 - rad * Math.sin(angle);
        d += (i === 0 ? 'M' : 'L') + px.toFixed(1) + ',' + py.toFixed(1);
      }
      return d + ' Z';
    }
    case 'hline':
      return `M0,${h/2} H${w}`;
    case 'vline':
      return `M${w/2},0 V${h}`;
    case 'arrow':
      return `M0,${h/2} H${w-12} L${w-12},${h/2-6} L${w},${h/2} L${w-12},${h/2+6} L${w-12},${h/2}`;
    default:
      return `M0,0 H${w} V${h} H0 Z`;
  }
}

/* ── Drawing Shape JointJS element ─────────────────────────────────────────── */
const DrawingShape = joint.dia.Element.define('network.DrawingShape', {
  attrs: {
    body: { refWidth: '100%', refHeight: '100%', fill: 'transparent', stroke: 'transparent', strokeWidth: 0 },
    shape: { fill: '#1a2a3a', stroke: '#4a9eff', strokeWidth: 2, strokeLinejoin: 'round' },
    label: { x: 10, y: 16, textAnchor: 'start', fontSize: 11, fontWeight: '600', fontFamily: 'Segoe UI, system-ui, sans-serif', fill: '#eaeaea' }
  },
}, {
  markup: [
    { tagName: 'rect', selector: 'body' },
    { tagName: 'path', selector: 'shape' },
    { tagName: 'text', selector: 'label' },
  ]
});

/* ── Text Label JointJS element ────────────────────────────────────────────── */
const TextLabel = joint.dia.Element.define('network.TextLabel', {
  attrs: {
    body: { refWidth: '100%', refHeight: '100%', fill: 'transparent', stroke: 'transparent', strokeWidth: 0, rx: 4, ry: 4 },
    label: {
      refX: '50%', refY: '50%',
      textAnchor: 'middle', textVerticalAnchor: 'middle',
      fontSize: 14, fontFamily: 'Segoe UI, system-ui, sans-serif',
      fill: '#eaeaea', fontWeight: 'normal',
    }
  },
}, {
  markup: [
    { tagName: 'rect', selector: 'body' },
    { tagName: 'text', selector: 'label' },
  ]
});

/* ── Initialize JointJS ─────────────────────────────────────────────────────── */
function initCanvas() {
  graph = new joint.dia.Graph();

  paper = new joint.dia.Paper({
    el: document.getElementById('canvas-container'),
    model: graph,
    width: 5000,
    height: 5000,
    gridSize: 10,
    drawGrid: { name: 'dot', args: { color: 'rgba(0,0,0,0.08)' } },
    background: { color: '#ffffff' },
    defaultLink: () => new joint.shapes.standard.Link({
      attrs: {
        line: {
          stroke: '#e94560',
          strokeWidth: 2,
          targetMarker: { type: 'classic', fill: '#e94560', size: 6 }
        }
      }
    }),
    validateConnection: () => true,
    snapLinks: { radius: 20 },
    linkPinning: false,
    interactive: { labelMove: false },
  });

  // Canvas is 5000x5000 — scrollable via .canvas-area overflow:auto

  // ── Canvas panning (drag blank area to pan) ──
  let _isPanning = false;
  let _panStart = { x: 0, y: 0 };
  let _panScrollStart = { x: 0, y: 0 };

  paper.on('blank:pointerdown', (evt) => {
    const canvasArea = document.querySelector('.canvas-area');
    if (!canvasArea) return;
    _isPanning = true;
    _panStart = { x: evt.clientX, y: evt.clientY };
    _panScrollStart = { x: canvasArea.scrollLeft, y: canvasArea.scrollTop };
    canvasArea.style.cursor = 'grabbing';
    evt.preventDefault();
  });

  document.addEventListener('mousemove', (evt) => {
    if (!_isPanning) return;
    const canvasArea = document.querySelector('.canvas-area');
    if (!canvasArea) return;
    const dx = evt.clientX - _panStart.x;
    const dy = evt.clientY - _panStart.y;
    canvasArea.scrollLeft = _panScrollStart.x - dx;
    canvasArea.scrollTop = _panScrollStart.y - dy;
  });

  document.addEventListener('mouseup', () => {
    if (_isPanning) {
      _isPanning = false;
      const canvasArea = document.querySelector('.canvas-area');
      if (canvasArea) canvasArea.style.cursor = '';
    }
  });

  // Selection (CR markup mode intercepts clicks when active)
  paper.on('element:pointerclick', (view, evt) => {
    if (_crHandleElementClick(view.model)) return;
    if (evt && evt.shiftKey) { _toggleMultiSelect(view.model, view); return; }
    _clearMultiSelect();
    selectCell(view.model);
  });
  paper.on('link:pointerclick', (view, evt) => {
    if (_crHandleLinkClick(view.model)) return;
    if (evt && evt.shiftKey) return; // shift+click on link: ignore
    _clearMultiSelect();
    selectCell(view.model);
  });
  paper.on('blank:pointerclick', () => { if (!_isPanning) { _clearMultiSelect(); deselectAll(); hideBlastContextMenu(); } });

  // Right-click on device → blast radius context menu
  paper.on('element:contextmenu', (view, evt) => {
    evt.preventDefault();
    selectCell(view.model);
    hideLinkContextMenu();
    showBlastContextMenu(evt.clientX, evt.clientY, view.model);
  });
  // Right-click on link → capture context menu
  paper.on('link:contextmenu', (view, evt) => {
    evt.preventDefault();
    selectCell(view.model);
    hideBlastContextMenu();
    showLinkContextMenu(evt.clientX, evt.clientY, view.model);
  });
  paper.on('blank:contextmenu', (view, evt) => { evt.preventDefault(); hideBlastContextMenu(); hideLinkContextMenu(); });
  document.addEventListener('click', () => { hideBlastContextMenu(); hideLinkContextMenu(); }, true);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { hideBlastContextMenu(); hideLinkContextMenu(); } });

  // Double-click link to annotate cable run
  paper.on('link:pointerdblclick', (view) => {
    openCableAnnotationDialog(view.model);
  });

  // Double-click to rename
  paper.on('element:pointerdblclick', (view) => {
    const cell = view.model;
    const newLabel = prompt('Rename object:', cell.attr('label/text') || '');
    if (newLabel !== null) {
      pushUndo();
      cell.attr('label/text', newLabel);
      if (selectedCell && selectedCell.id === cell.id) {
        document.getElementById('cfg-label').value = newLabel;
      }
      markDirty();
    }
  });

  // Graph change → autosave
  graph.on('change add remove', () => markDirty());

  // Load existing topology
  if (currentTopoId && currentTopoId !== 'new') {
    loadTopology(currentTopoId);
  }

  // Keyboard shortcuts
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); saveTopology(); }
    if ((e.ctrlKey || e.metaKey) && e.key === 'z') { e.preventDefault(); undoAction(); }
    if ((e.ctrlKey || e.metaKey) && e.key === 'y') { e.preventDefault(); redoAction(); }
    if ((e.ctrlKey || e.metaKey) && e.key === 'd') { e.preventDefault(); duplicateSelected(); }
    if (e.key === 'Escape' && !document.getElementById('cr-just-dialog').classList.contains('hidden')) {
      crJustCancel();
      return;
    }
    if (e.key === 'Delete' || e.key === 'Backspace') {
      if (document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA') {
        if (_selectedCells.length > 0) { _deleteMultiSelected(); }
        else if (selectedCell) { deleteSelected(); }
      }
    }
  });

  // Canvas node hover tooltips
  initCanvasTooltips();

  updateStatusBar();
  initColorPalettes();
  _startPeriodicBackup();
  // Check for crash recovery after topology loads
  setTimeout(() => _checkLocalRecovery(), 2000);
  setStatus('Ready — drag objects from the palette to begin');
}

/* ── Blast Radius Context Menu ────────────────────────────────────────────────── */
let _blastCtxNodeId = null;

/* ── Policy Panel State ─────────────────────────────────────────────────────── */
let _policyNodeId = null;

/* ── Link Policy Panel State ────────────────────────────────────────────────── */
let _policyLinkId = null;

function showBlastContextMenu(x, y, cell) {
  const menu = document.getElementById('blast-ctx-menu');
  const nameEl = document.getElementById('blast-ctx-device-name');
  if (!menu || !cell.isElement()) return;
  _blastCtxNodeId = cell.id;
  const label = cell.attr('label/text') || cell.id;
  nameEl.textContent = label;

  // Position menu — keep within viewport
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const mw = 224;
  const mh = 150;
  menu.style.left = (x + mw > vw ? vw - mw - 8 : x) + 'px';
  menu.style.top  = (y + mh > vh ? vh - mh - 8 : y) + 'px';
  menu.style.display = 'block';
}

function hideBlastContextMenu() {
  const menu = document.getElementById('blast-ctx-menu');
  if (menu) menu.style.display = 'none';
}

function openPolicyPanel(nodeId) {
  hideBlastContextMenu();
  nodeId = nodeId || _blastCtxNodeId;
  if (!nodeId) return;
  const cell = graph.getCell(nodeId);
  if (!cell || !cell.isElement()) return;
  _policyNodeId = nodeId;

  const policy = (cell.get('configData') || {}).policy || {};
  document.getElementById('policy-node-title').textContent = cell.attr('label/text') || nodeId;
  document.getElementById('pol-classification').value = policy.classification || '';
  document.getElementById('pol-zone').value = policy.zone || '';
  document.getElementById('pol-segmentation').value = policy.segmentation || '';
  document.getElementById('pol-acl-inbound').value = policy.acl_inbound || '';
  document.getElementById('pol-acl-outbound').value = policy.acl_outbound || '';
  document.getElementById('pol-encryption').checked = !!policy.encryption_required;
  document.getElementById('pol-requires-kg').checked = !!policy.requiresKG;
  document.getElementById('pol-notes').value = policy.notes || '';
  _updateKgAutoBadge(policy.classification || '');

  document.getElementById('policy-overlay').classList.remove('hidden');
}

function closePolicyPanel() {
  document.getElementById('policy-overlay').classList.add('hidden');
  _policyNodeId = null;
}

function onPolicyFieldChange(key, val) {
  const id = _policyNodeId || _blastCtxNodeId;
  const cell = id && graph.getCell(id);
  if (!cell || !cell.isElement()) return;
  const config = cell.get('configData') || {};
  config.policy = config.policy || {};
  config.policy[key] = val;
  cell.set('configData', config);
  markDirty();
}

function onPolicyClassificationChange(val) {
  onPolicyFieldChange('classification', val);
  // Auto-set requiresKG for IL4+ (CUI, SECRET, TOP SECRET — classification rank >= 1)
  if (_classRank(val) >= 1) {
    onPolicyFieldChange('requiresKG', true);
    document.getElementById('pol-requires-kg').checked = true;
  }
  _updateKgAutoBadge(val);
}

function _updateKgAutoBadge(cls) {
  const badge = document.getElementById('pol-kg-auto-badge');
  if (badge) badge.style.display = _classRank(cls) >= 1 ? 'inline' : 'none';
}

/* ── R-NAME: device-type → naming prefix ────────────────────────────────── */
const _RNAME_PREFIX = {
  'router':'rtr','switch-l2':'sw','switch-l3':'sw3','firewall':'fw',
  'load-balancer':'lb','wap':'ap','server':'srv','patch-panel':'pp',
  'endpoint-pc':'pc','endpoint-phone':'ph','endpoint-iot':'iot',
  'endpoint-camera':'cam','wlc':'wlc','sdwan-edge':'sdw',
  'network-tap':'tap','siem':'siem','hsm':'hsm',
  'type1-encryptor':'enc','macsec':'mac','vrf':'vrf','vlan':'vl',
  'subnet':'snet','security-zone':'sz','gre-tunnel':'tun',
  'ipsec-tunnel':'tun','tls-terminator':'tls',
};

function _rNameCheck(label, nodeType) {
  const VALID = /^[a-z][a-z0-9]*(-[a-z0-9]+)+$/;
  if (VALID.test(label)) return null;

  const prefix = _RNAME_PREFIX[nodeType] || 'dev';
  const cleaned = label.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  const suggestedName = cleaned.includes('-') ? cleaned : `site-${prefix}-01`;
  return {
    rule: 'R-NAME',
    severity: 'warning',
    message: `"${label}" violates naming convention. Labels must be lowercase alphanumeric with hyphens (e.g. site-${prefix}-01).`,
    suggestedName,
    autoFix: true,
  };
}

function _renderAuditViolations(violations, nodeId) {
  const list = document.getElementById('audit-violations-list');
  if (!list) return;
  if (!violations.length) {
    list.innerHTML = '<div style="color:#27ae60;font-size:13px;padding:8px 0;">&#x2705; No policy violations found.</div>';
    return;
  }
  list.innerHTML = violations.map(v => `
    <div style="background:#1a1a2e;border:1px solid ${v.severity==='error'?'#e74c3c':'#f39c12'};border-radius:6px;padding:12px;margin-bottom:10px;">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
        <span style="font-size:10px;font-weight:700;padding:1px 7px;border-radius:10px;background:${v.severity==='error'?'#e74c3c':'#f39c12'};color:#000;">${v.rule}</span>
        <span style="font-size:11px;color:#7a8cb0;text-transform:uppercase;">${v.severity}</span>
      </div>
      <div style="font-size:12px;color:#cdd6f4;margin-bottom:8px;">${v.message}</div>
      ${v.suggestedName ? `
        <div style="font-size:11px;color:#7a8cb0;margin-bottom:8px;">
          Auto-fix suggestion: <span style="font-family:monospace;color:#2ecc71;font-weight:600;">${v.suggestedName}</span>
        </div>
        ${v.autoFix ? `<button onclick="_applyAuditAutoFix('${nodeId}','${v.suggestedName}')"
          style="font-size:11px;padding:4px 12px;background:#2ecc71;color:#000;border:none;border-radius:4px;cursor:pointer;font-weight:600;">
          &#x26A1; Auto Fix</button>` : ''}
      ` : ''}
    </div>
  `).join('');
}

function _applyAiCorrectedGlow(cell) {
  const view = paper.findViewByModel(cell);
  if (!view) return;
  const corrected = (cell.get('configData') || {})._aiCorrected === true;
  view.el.classList.toggle('ai-corrected-node', corrected);
}

function _applyAuditAutoFix(nodeId, suggestedName) {
  const cell = graph.getCell(nodeId);
  if (!cell || !cell.isElement()) return;
  cell.attr('label/text', suggestedName);
  markDirty();
  openAuditPanel(nodeId);
}

function applyAutoFix(nodeId, ruleId) {
  const violations = auditResults[nodeId] || [];
  const violation = violations.find(v => v.rule === ruleId);
  if (!violation || !violation.autoFix) return;

  const cell = graph.getCell(nodeId);
  if (!cell || !cell.isElement()) return;

  pushUndo();

  const config = cell.get('configData') || {};

  let oldValue, newValue;
  if (ruleId === 'R-NAME' && violation.suggestedName) {
    oldValue = cell.attr('label/text') || config.hostname || '';
    newValue = violation.suggestedName;
    cell.attr('label/text', newValue);
    config.hostname = newValue;
  } else if (violation.suggestedIp) {
    oldValue = config.ip || config.ipv4 || '';
    newValue = violation.suggestedIp;
    config.ip = newValue;
    config.ipv4 = newValue;
    cell.attr('iplabel/text', newValue);
  }

  _aiCorrections.push({ nodeId, ruleId, timestamp: new Date().toISOString(), oldValue, newValue });

  config._aiCorrected = true;
  cell.set('configData', config);
  _applyAiCorrectedGlow(cell);
  markDirty();
  openAuditPanel(nodeId);
}

function autoFixAll() {
  const snapshot = Object.entries(auditResults);
  for (const [nodeId, violations] of snapshot) {
    for (const v of violations) {
      if (v.autoFix) {
        applyAutoFix(nodeId, v.rule);
      }
    }
  }
}

function openAuditPanel(nodeId) {
  hideBlastContextMenu();
  nodeId = nodeId || _blastCtxNodeId;
  if (!nodeId) return;
  const cell = graph.getCell(nodeId);
  if (!cell || !cell.isElement()) return;

  const label = cell.attr('label/text') || nodeId;
  const nodeType = cell.get('type') || '';

  document.getElementById('audit-node-title').textContent = label;

  const violations = [];
  const rName = _rNameCheck(label, nodeType);
  if (rName) violations.push(rName);

  auditResults[nodeId] = violations;
  _renderAuditViolations(violations, nodeId);
  document.getElementById('audit-overlay').classList.remove('hidden');
}

function closeAuditPanel() {
  document.getElementById('audit-overlay').classList.add('hidden');
}

/* ── Link Policy Panel ───────────────────────────────────────────────────────── */

function openLinkPolicyPanel() {
  hideLinkContextMenu();
  const linkId = _capLinkId;
  if (!linkId) return;
  const link = graph.getCell(linkId);
  if (!link || !link.isLink()) return;
  _policyLinkId = linkId;

  const lc = link.get('linkConfig') || {};
  const policy = lc.policy || {};

  // Resolve classification: inherit lower of src/dst if not explicitly set
  const srcId = (link.get('source') || {}).id;
  const tgtId = (link.get('target') || {}).id;
  const srcCell = srcId && graph.getCell(srcId);
  const tgtCell = tgtId && graph.getCell(tgtId);
  const srcCls = (srcCell && (srcCell.get('configData') || {}).policy || {}).classification || '';
  const tgtCls = (tgtCell && (tgtCell.get('configData') || {}).policy || {}).classification || '';

  let inheritedCls = '';
  let inheritedNote = '';
  if (srcCls && tgtCls) {
    // Inherit the lower classification (less restrictive endpoint governs the link floor)
    inheritedCls = _classRank(srcCls) <= _classRank(tgtCls) ? srcCls : tgtCls;
    inheritedNote = `Inherited: ${srcCls} ↔ ${tgtCls} → floor ${inheritedCls}`;
  } else if (srcCls || tgtCls) {
    inheritedCls = srcCls || tgtCls;
    inheritedNote = `Inherited from ${srcCls ? 'source' : 'target'}: ${inheritedCls}`;
  }

  const effectiveCls = policy.classification || inheritedCls;

  // Cross-domain: src and dst have different non-empty classification ranks
  const crossDomain = srcCls && tgtCls && _classRank(srcCls) !== _classRank(tgtCls);
  const encRequired = policy.encryption_required !== undefined
    ? policy.encryption_required
    : crossDomain;

  // Populate title
  const srcName = (srcCell && srcCell.attr('label/text')) || srcId || '?';
  const tgtName = (tgtCell && tgtCell.attr('label/text')) || tgtId || '?';
  document.getElementById('lp-link-title').textContent = `${srcName} → ${tgtName}`;

  // Inherited badge
  const badge = document.getElementById('lp-inherited-badge');
  if (inheritedNote) {
    badge.textContent = inheritedNote;
    badge.style.display = 'block';
  } else {
    badge.style.display = 'none';
  }

  document.getElementById('lp-classification').value = effectiveCls;
  document.getElementById('lp-encryption').checked = !!encRequired;
  document.getElementById('lp-enc-auto-badge').style.display = crossDomain ? 'inline' : 'none';

  const protocolVal = policy.protocol_allowed || '';
  const knownProtocols = ['TLS','IPsec','MACsec','HTTPS','SSH','OSPF','BGP','EIGRP','IS-IS'];
  if (!protocolVal || knownProtocols.includes(protocolVal)) {
    document.getElementById('lp-protocol').value = protocolVal;
    document.getElementById('lp-protocol-custom-group').style.display = 'none';
  } else {
    document.getElementById('lp-protocol').value = 'custom';
    document.getElementById('lp-protocol-custom').value = protocolVal;
    document.getElementById('lp-protocol-custom-group').style.display = 'block';
  }

  document.getElementById('link-policy-overlay').classList.remove('hidden');
}

function closeLinkPolicyPanel() {
  document.getElementById('link-policy-overlay').classList.add('hidden');
  _policyLinkId = null;
}

function onLinkPolicyFieldChange(key, val) {
  const link = _policyLinkId && graph.getCell(_policyLinkId);
  if (!link || !link.isLink()) return;
  const lc = link.get('linkConfig') || {};
  lc.policy = lc.policy || {};
  lc.policy[key] = val;
  link.set('linkConfig', lc);
  markDirty();
}

function onLinkPolicyClassificationChange(val) {
  onLinkPolicyFieldChange('classification', val);
  // Re-evaluate cross-domain encryption auto-flag
  const link = _policyLinkId && graph.getCell(_policyLinkId);
  if (!link) return;
  const srcId = (link.get('source') || {}).id;
  const tgtId = (link.get('target') || {}).id;
  const srcCell = srcId && graph.getCell(srcId);
  const tgtCell = tgtId && graph.getCell(tgtId);
  const srcCls = (srcCell && (srcCell.get('configData') || {}).policy || {}).classification || '';
  const tgtCls = (tgtCell && (tgtCell.get('configData') || {}).policy || {}).classification || '';
  const crossDomain = srcCls && tgtCls && _classRank(srcCls) !== _classRank(tgtCls);
  const encBox = document.getElementById('lp-encryption');
  if (crossDomain && !encBox.checked) {
    encBox.checked = true;
    onLinkPolicyFieldChange('encryption_required', true);
  }
  document.getElementById('lp-enc-auto-badge').style.display = crossDomain ? 'inline' : 'none';
}

function onLinkPolicyProtocolChange(val) {
  const customGroup = document.getElementById('lp-protocol-custom-group');
  if (val === 'custom') {
    customGroup.style.display = 'block';
    // Don't save 'custom' as the value — wait for the text input
    const customInput = document.getElementById('lp-protocol-custom');
    if (customInput.value) onLinkPolicyFieldChange('protocol_allowed', customInput.value);
  } else {
    customGroup.style.display = 'none';
    onLinkPolicyFieldChange('protocol_allowed', val);
  }
}

/* ── Link Capture Context Menu ──────────────────────────────────────────────── */
let _capLinkId = null;
let _capLinkLabel = '';
let _capCurrentId = null;
let _capPollTimer = null;
let _capElapsedTimer = null;
let _capElapsedSec = 0;

function showLinkContextMenu(x, y, link) {
  const menu = document.getElementById('link-ctx-menu');
  if (!menu || !link.isLink()) return;
  _capLinkId = link.id;

  // Build label from source and target node names
  const srcId = link.get('source') && link.get('source').id;
  const tgtId = link.get('target') && link.get('target').id;
  const srcCell = srcId && graph.getCell(srcId);
  const tgtCell = tgtId && graph.getCell(tgtId);
  const srcName = (srcCell && srcCell.attr('label/text')) || srcId || '?';
  const tgtName = (tgtCell && tgtCell.attr('label/text')) || tgtId || '?';
  const proto = link.get('protocol') || '';
  _capLinkLabel = `${srcName} → ${tgtName}${proto ? ' [' + proto + ']' : ''}`;

  document.getElementById('link-ctx-link-label').textContent = _capLinkLabel;

  const vw = window.innerWidth, vh = window.innerHeight;
  const mw = 224, mh = 170;
  menu.style.left = (x + mw > vw ? vw - mw - 8 : x) + 'px';
  menu.style.top  = (y + mh > vh ? vh - mh - 8 : y) + 'px';
  menu.style.display = 'block';
}

function hideLinkContextMenu() {
  const menu = document.getElementById('link-ctx-menu');
  if (menu) menu.style.display = 'none';
}

/** Called from the link context menu — starts a new capture and opens viewer. */
function startLinkCapture() {
  hideLinkContextMenu();
  if (!_capLinkId) return;
  openCapturePanel();
  _doStartCapture(_capLinkId, _capLinkLabel);
}

/** Called from inside the capture panel — starts a new capture on the same link. */
function startLinkCaptureFromPanel() {
  if (!_capLinkId) return;
  _doStartCapture(_capLinkId, _capLinkLabel);
}

/** Open the capture viewer without starting a new capture (show history). */
function showCaptures() {
  hideLinkContextMenu();
  if (!_capLinkId) return;
  openCapturePanel();
  _loadCaptureHistory(_capLinkId);
}

function openCapturePanel() {
  document.getElementById('capture-overlay').classList.remove('hidden');
  document.getElementById('cap-link-label').textContent = _capLinkLabel;
  document.getElementById('cap-status-badge').textContent = '';
  document.getElementById('cap-status-badge').style.background = '';
  document.getElementById('cap-size').textContent = '—';
  document.getElementById('cap-sha').textContent = '—';
  document.getElementById('cap-sha').title = '';
  document.getElementById('cap-expiry').textContent = '—';
  document.getElementById('cap-id-display').textContent = '—';
  document.getElementById('cap-stop-btn').style.display = 'none';
  document.getElementById('cap-download-btn').style.display = 'none';
  document.getElementById('cap-spinner').style.display = 'none';
}

function closeCapturePanel() {
  document.getElementById('capture-overlay').classList.add('hidden');
  _stopCapturePolling();
}

function _doStartCapture(linkId, linkLabel) {
  _stopCapturePolling();
  _capCurrentId = null;

  // Determine src/dst/protocol from link model
  let srcLabel = '', dstLabel = '', protocol = '';
  if (linkId) {
    const link = graph.getCell(linkId);
    if (link) {
      const srcId = link.get('source') && link.get('source').id;
      const tgtId = link.get('target') && link.get('target').id;
      const srcCell = srcId && graph.getCell(srcId);
      const tgtCell = tgtId && graph.getCell(tgtId);
      srcLabel = (srcCell && srcCell.attr('label/text')) || srcId || '';
      dstLabel = (tgtCell && tgtCell.attr('label/text')) || tgtId || '';
      protocol = link.get('protocol') || '';
    }
  }

  // Show running state immediately
  _setCaptureStatusUI({ status: 'running', id: '…', size_bytes: 0, sha256: '', expiry_at: '', src_label: srcLabel, dst_label: dstLabel });

  fetch(`/network/api/captures`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      link_id: linkId,
      topology_id: currentTopoId,
      src_label: srcLabel,
      dst_label: dstLabel,
      protocol: protocol,
    }),
  })
    .then(r => r.json())
    .then(data => {
      if (data.error) { _setCaptureError(data.error); return; }
      _capCurrentId = data.id;
      document.getElementById('cap-id-display').textContent = data.id.slice(0, 8) + '…';
      _startCapturePolling(data.id);
      _loadCaptureHistory(linkId);
    })
    .catch(err => _setCaptureError(String(err)));
}

function _startCapturePolling(capId) {
  _capElapsedSec = 0;
  document.getElementById('cap-spinner').style.display = '';
  document.getElementById('cap-elapsed').textContent = '0';
  document.getElementById('cap-stop-btn').style.display = '';
  document.getElementById('cap-download-btn').style.display = 'none';

  _capElapsedTimer = setInterval(() => {
    _capElapsedSec++;
    const el = document.getElementById('cap-elapsed');
    if (el) el.textContent = String(_capElapsedSec);
  }, 1000);

  _capPollTimer = setInterval(() => {
    fetch(`/network/api/captures/${capId}`)
      .then(r => r.json())
      .then(data => {
        _setCaptureStatusUI(data);
        if (data.status !== 'running') {
          _stopCapturePolling();
          _loadCaptureHistory(_capLinkId);
        }
      })
      .catch(() => {});
  }, 2000);
}

function _stopCapturePolling() {
  if (_capPollTimer) { clearInterval(_capPollTimer); _capPollTimer = null; }
  if (_capElapsedTimer) { clearInterval(_capElapsedTimer); _capElapsedTimer = null; }
}

function _setCaptureStatusUI(data) {
  const badge = document.getElementById('cap-status-badge');
  const spinner = document.getElementById('cap-spinner');
  const stopBtn = document.getElementById('cap-stop-btn');
  const dlBtn = document.getElementById('cap-download-btn');

  if (data.status === 'running') {
    badge.textContent = '● RUNNING';
    badge.style.background = '#e67e22';
    badge.style.color = '#fff';
    spinner.style.display = '';
    stopBtn.style.display = '';
    dlBtn.style.display = 'none';
  } else if (data.status === 'complete') {
    badge.textContent = '✓ COMPLETE';
    badge.style.background = '#2ecc71';
    badge.style.color = '#1a1a2e';
    spinner.style.display = 'none';
    stopBtn.style.display = 'none';
    dlBtn.style.display = '';
    const bytes = data.size_bytes || 0;
    document.getElementById('cap-size').textContent = bytes < 1024 ? `${bytes} B` : `${(bytes/1024).toFixed(1)} KB`;
    const sha = data.sha256 || '';
    document.getElementById('cap-sha').textContent = sha ? sha.slice(0, 12) + '…' : '—';
    document.getElementById('cap-sha').title = sha;
    document.getElementById('cap-expiry').textContent = (data.expiry_at || '—').replace('T', ' ').replace('Z', ' UTC').slice(0, 20);
    if (data.id) document.getElementById('cap-id-display').textContent = data.id.slice(0, 8) + '…';
  } else {
    badge.textContent = '✕ ERROR';
    badge.style.background = '#e74c3c';
    badge.style.color = '#fff';
    spinner.style.display = 'none';
    stopBtn.style.display = 'none';
    dlBtn.style.display = 'none';
  }
}

function _setCaptureError(msg) {
  _stopCapturePolling();
  const badge = document.getElementById('cap-status-badge');
  badge.textContent = '✕ ERROR';
  badge.style.background = '#e74c3c';
  badge.style.color = '#fff';
  document.getElementById('cap-spinner').style.display = 'none';
  document.getElementById('cap-stop-btn').style.display = 'none';
  document.getElementById('cap-size').textContent = msg;
}

function stopCurrentCapture() {
  if (!_capCurrentId) return;
  _stopCapturePolling();
  fetch(`/network/api/captures/${_capCurrentId}/stop`, { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      _setCaptureStatusUI({ ...(data || {}), id: _capCurrentId });
      return fetch(`/network/api/captures/${_capCurrentId}`);
    })
    .then(r => r && r.json())
    .then(data => { if (data) { _setCaptureStatusUI(data); _loadCaptureHistory(_capLinkId); } })
    .catch(() => {});
}

function downloadCurrentCapture() {
  if (!_capCurrentId) return;
  window.location.href = `/network/api/captures/${_capCurrentId}/download`;
}

function _loadCaptureHistory(linkId) {
  if (!linkId) return;
  const list = document.getElementById('cap-history-list');
  if (!list) return;
  list.innerHTML = '<div style="color:#7a8cb0;font-size:12px;text-align:center;padding:12px;">Loading…</div>';

  fetch(`/network/api/captures?link_id=${encodeURIComponent(linkId)}`)
    .then(r => r.json())
    .then(captures => {
      if (!captures.length) {
        list.innerHTML = '<div style="color:#7a8cb0;font-size:12px;text-align:center;padding:16px;">No captures yet.</div>';
        return;
      }
      list.innerHTML = captures.map(c => {
        const isRunning = c.status === 'running';
        const ts = (c.created_at || '').replace('T', ' ').replace('Z', '').slice(0, 16);
        const bytes = c.size_bytes || 0;
        const sizeStr = isRunning ? '…' : (bytes < 1024 ? `${bytes} B` : `${(bytes/1024).toFixed(1)} KB`);
        const badgeColor = isRunning ? '#e67e22' : (c.status === 'complete' ? '#2ecc71' : '#e74c3c');
        const badgeText = isRunning ? '● RUN' : (c.status === 'complete' ? '✓' : '✕');
        return `<div style="display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:4px;
          background:#12122a;margin-bottom:4px;font-size:11px;">
          <span style="background:${badgeColor};color:${isRunning ? '#fff' : '#1a1a2e'};
            padding:1px 5px;border-radius:8px;font-size:10px;font-weight:600;">${badgeText}</span>
          <span style="color:#cdd6f4;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;"
            title="${_escHtml(c.id)}">${ts} · ${sizeStr}</span>
          ${c.status === 'complete'
            ? `<a href="/network/api/captures/${c.id}/download" style="color:#2ecc71;text-decoration:none;font-size:10px;">&#x2B07; .pcap</a>`
            : isRunning
              ? `<button onclick="_capCurrentId='${c.id}';stopCurrentCapture();" style="background:#e74c3c;border:none;color:#fff;padding:1px 6px;border-radius:3px;font-size:10px;cursor:pointer;">Stop</button>`
              : ''}
        </div>`;
      }).join('');
    })
    .catch(() => { list.innerHTML = '<div style="color:#e74c3c;font-size:12px;text-align:center;padding:12px;">Failed to load.</div>'; });
}

/* ── Canvas Tooltips ──────────────────────────────────────────────────────────── */
function initCanvasTooltips() {
  // Use the shared enhanced tooltip utility (canvas-tooltips.js)
  if (typeof initEnhancedTooltips === 'function') {
    initEnhancedTooltips(paper, graph, getStyle);
  }
}

/* ── Topology Legend ────────────────────────────────────────────────────────── */
var _legendVisible = false;

function toggleTopologyLegend() {
  if (_legendVisible) { hideTopologyLegend(); return; }
  showTopologyLegend();
}

function showTopologyLegend() {
  let el = document.getElementById('topo-legend');
  if (!el) {
    el = document.createElement('div');
    el.id = 'topo-legend';
    el.className = 'topo-legend';
    const container = document.getElementById('canvas-container') || document.body;
    container.appendChild(el);
  }
  // Scan canvas for node types and edge protocols in use
  const nodeTypes = {};
  const edgeProtocols = {};
  graph.getElements().forEach(cell => {
    const t = cell.get('nodeType');
    if (t && !nodeTypes[t]) {
      nodeTypes[t] = getStyle(t);
    }
  });
  graph.getLinks().forEach(link => {
    const p = link.get('protocol');
    if (p && !edgeProtocols[p]) edgeProtocols[p] = true;
  });

  // Group node types by category
  const categories = {};
  Object.entries(nodeTypes).forEach(([type, style]) => {
    let cat = 'Devices';
    if (type.startsWith('aws-')) cat = 'AWS';
    else if (type.startsWith('az-')) cat = 'Azure';
    else if (type.startsWith('gcp-')) cat = 'GCP';
    else if (type.startsWith('oci-')) cat = 'OCI';
    else if (['roadm','oadm','edfa','transponder','odf','sonet-adm','olt'].includes(type)) cat = 'Optical / DWDM';
    else if (['mpls-pe','mpls-p','route-reflector','pop'].includes(type)) cat = 'MPLS / Carrier';
    else if (['firewall','waf','ids','ips','siem','fips-140-l3'].includes(type)) cat = 'Security';
    else if (['switch-l2','switch-l3','router'].includes(type)) cat = 'Network';
    else if (['media-fiber','media-optical','patch-panel-fiber'].includes(type)) cat = 'Physical / Media';
    if (!categories[cat]) categories[cat] = [];
    categories[cat].push({ type, style });
  });

  let html = '<div class="topo-legend-title">Legend <button class="topo-legend-close" onclick="hideTopologyLegend()">&times;</button></div>';

  // Node types by category
  Object.entries(categories).sort(([a],[b]) => a.localeCompare(b)).forEach(([cat, items]) => {
    html += `<div class="topo-legend-section">${cat}</div>`;
    items.sort((a,b) => a.style.label.localeCompare(b.style.label)).forEach(({ type, style }) => {
      const sym = style.symbol || type.charAt(0).toUpperCase();
      html += `<div class="topo-legend-item"><div class="topo-legend-swatch" style="background:${style.fill || '#1a2a3a'};border:1px solid ${style.stroke || '#555'};">${sym}</div>${style.label}</div>`;
    });
  });

  // Edge protocols
  const protocols = Object.keys(edgeProtocols).filter(p => p);
  if (protocols.length) {
    html += '<div class="topo-legend-section">Protocols / Links</div>';
    const protoColors = {
      'BGP': '#3498db', 'eBGP': '#2980b9', 'iBGP': '#1abc9c',
      'OSPF': '#e67e22', 'OSPF/IS-IS': '#e67e22',
      'DWDM': '#9b59b6', 'DWDM BLSR': '#8e44ad',
      'IPsec': '#e74c3c', 'IPsec/BGP': '#c0392b', 'IPsec Type1': '#c0392b',
      'MPLS': '#2ecc71', '802.1Q': '#f39c12',
    };
    protocols.sort().forEach(p => {
      const color = protoColors[p] || '#7f8c8d';
      const dashed = p.includes('backup') || p.includes('protect');
      html += `<div class="topo-legend-item"><div class="topo-legend-line${dashed ? ' dashed' : ''}" style="border-color:${color};"></div>${p}</div>`;
    });
  }

  el.innerHTML = html;
  el.style.display = 'block';
  _legendVisible = true;
}

function hideTopologyLegend() {
  const el = document.getElementById('topo-legend');
  if (el) el.style.display = 'none';
  _legendVisible = false;
}

// Expose globally
if (typeof window.ICDEV === 'undefined') window.ICDEV = {};
window.ICDEV.toggleTopologyLegend = toggleTopologyLegend;
window.ICDEV.showTopologyLegend = showTopologyLegend;
window.ICDEV.hideTopologyLegend = hideTopologyLegend;

/* ── Create a node on the canvas ─────────────────────────────────────────────── */
function createNode(type, x, y, label, nodeId, configData) {
  const style = getStyle(type);
  const displayLabel = label || style.label;
  const config = configData || {};

  // Drawing shapes (including callout-bubble)
  if (style.shape && ['rect','roundedrect','circle','ellipse','diamond','triangle','hexagon','star','hline','vline','arrow'].includes(style.shape)) {
    const isLine = ['hline','vline','arrow'].includes(style.shape);
    const isCallout = (type === 'callout-bubble');
    const w = config._width  || (isCallout ? 40 : isLine ? (style.shape === 'vline' ? 20 : 160) : (['circle','star'].includes(style.shape) ? 80 : 120));
    const h = config._height || (isCallout ? 40 : isLine ? (style.shape === 'hline' || style.shape === 'arrow' ? 20 : 100) : (['circle','star'].includes(style.shape) ? 80 : 80));
    const shapePath = drawingShapePath(style.shape, w, h);

    // Label position
    const isLargeZone = !isCallout && (w > 150 || h > 100);
    const labelAttrs = isCallout
      ? { refX: '50%', refY: '50%', textAnchor: 'middle', textVerticalAnchor: 'middle', fontSize: 16, fontWeight: 'bold' }
      : isLargeZone
        ? { x: 10, y: 18, textAnchor: 'start', fontSize: 11, fontWeight: '600' }
        : { refX: '50%', refY: '50%', textAnchor: 'middle', textVerticalAnchor: 'middle', fontSize: 10, fontWeight: 'normal' };

    const node = new DrawingShape({
      id: nodeId || joint.util.uuid(),
      position: { x: x || 100, y: y || 100 },
      size: { width: w, height: h },
      attrs: {
        shape: {
          d: shapePath,
          fill: isLine ? 'none' : (config._fill || style.fill),
          stroke: isCallout ? 'none' : (config._stroke || style.stroke),
          strokeWidth: config._strokeWidth || (isCallout ? 0 : 2),
          fillOpacity: isCallout ? 1.0 : (config._fillOpacity || 0.6),
        },
        label: {
          text: isLine ? '' : (isCallout ? displayLabel : (displayLabel === style.label ? '' : displayLabel)),
          fill: config._textColor || (isCallout ? '#ffffff' : (config._stroke || style.stroke)),
          ...labelAttrs,
        }
      }
    });
    node.set('nodeType', type);
    node.set('configData', config);
    graph.addCell(node);
    _applyNodeRotation(node);
    return node;
  }

  // Text labels
  if (style.shape && ['text','heading','badge'].includes(style.shape)) {
    const isHeading = style.shape === 'heading';
    const isBadge = style.shape === 'badge';
    const defaultText = displayLabel === style.label ? 'Double-click to edit' : displayLabel;
    const w = config._width || (isHeading ? 250 : isBadge ? 120 : 200);
    const h = config._height || (isHeading ? 36 : isBadge ? 28 : 30);

    const node = new TextLabel({
      id: nodeId || joint.util.uuid(),
      position: { x: x || 100, y: y || 100 },
      size: { width: w, height: h },
      attrs: {
        body: {
          fill: isBadge ? (config._fill || style.fill) : 'transparent',
          stroke: isBadge ? (config._stroke || style.stroke) : 'transparent',
          strokeWidth: isBadge ? 1 : 0,
          rx: isBadge ? 12 : 0,
          ry: isBadge ? 12 : 0,
        },
        label: {
          text: defaultText,
          fill: config._textColor || '#eaeaea',
          fontSize: isHeading ? 20 : isBadge ? 11 : 14,
          fontWeight: isHeading ? 'bold' : isBadge ? '600' : 'normal',
          fontFamily: isHeading ? 'Segoe UI, system-ui, sans-serif' : isBadge ? 'Cascadia Code, Consolas, monospace' : 'Segoe UI, system-ui, sans-serif',
        }
      }
    });
    node.set('nodeType', type);
    node.set('configData', config);
    graph.addCell(node);
    _applyNodeRotation(node);
    return node;
  }

  // Use config dimensions if provided, otherwise defaults
  const isGroup = (type === 'group-site');
  const isInfra = (type === 'pdu' || type === 'ups' || type === 'patch-panel');
  const w = config._width || (isGroup ? 300 : (isInfra ? 90 : 110));
  const h = config._height || (isGroup ? 200 : (isInfra ? 50 : 70));

  // Try Cisco traditional stencil (filled shape + white detail)
  const stencil = isGroup ? null : getCiscoStencil(type);

  const node = new NetworkNode({
    id: nodeId || joint.util.uuid(),
    position: { x: x || 100, y: y || 100 },
    size: { width: w, height: h },
    attrs: {
      body: {
        fill: config._fill || style.fill,
        stroke: config._stroke || style.stroke,
        strokeWidth: isGroup ? (config._strokeWidth || 2) : (stencil ? 1 : 2),
        strokeOpacity: isGroup ? 0.8 : (stencil ? 0.3 : 1),
        strokeDasharray: isGroup ? '8 4' : 'none',
        rx: isGroup ? 12 : (stencil ? 8 : 6),
        ry: isGroup ? 12 : (stencil ? 8 : 6),
      },
      stencilGroup: stencil ? {
        transform: 'translate(19, 3) scale(1.5)',
      } : { display: 'none' },
      stencilBody: stencil ? {
        d: stencil.body,
        // For custom-fill nodes (annotation/internet/legend):
        //   - Stencils WITH detail lines (server, router, firewall): semi-transparent dark
        //     overlay so the body shape creates a 3rd contrast layer (card bg → darker body
        //     area → white detail strokes) matching the visual quality of standard nodes.
        //   - Stencils WITHOUT detail (cloud): white fill so the closed shape is the icon.
        // Standard NODE_STYLES nodes (no _fill) fall back to style.stroke as designed.
        fill: config._fill
          ? (stencil.detail ? 'rgba(0,0,0,0.25)' : '#ffffff')
          : (config._stroke || style.stroke),
      } : { d: '' },
      stencilDetail: stencil ? {
        d: stencil.detail || '',
        fill: 'none',
        stroke: '#ffffff',
        strokeWidth: 1.5,
      } : { d: '' },
      symbol: {
        text: stencil ? '' : style.symbol,
        fill: config._stroke || style.stroke,
      },
      label: isGroup ? {
        text: displayLabel,
        fill: config._textColor || '#333',
        refX: 12, refY: 18,
        textAnchor: 'start',
        fontSize: 12,
        fontWeight: '700',
        letterSpacing: '0.5px',
      } : {
        text: displayLabel,
        fill: config._textColor || '#eaeaea',
      }
    }
  });

  node.set('nodeType', type);
  node.set('configData', config);

  // Populate sublabel (location/site) and iplabel (IP) from config — skip for groups
  if (!isGroup) {
    const loc = config.location || config.site || config.rack || '';
    const ip = config.ipv4 || config.ip || config.ip_address || '';
    if (loc) node.attr('sublabel/text', loc);
    if (ip) node.attr('iplabel/text', ip);
  }

  graph.addCell(node);
  _applyNodeRotation(node);
  return node;
}

/* ── Link Style by Protocol ─────────────────────────────────────────────────── */
const LINK_STYLES = {
  // Tunnels — dashed, curved
  'GRE':      { stroke: '#f9e79f', dash: '10,5',  width: 2, curve: true },
  'GRE/IPSec':{ stroke: '#f7dc6f', dash: '10,5',  width: 2, curve: true },
  'IPSec':    { stroke: '#f7dc6f', dash: '8,4',   width: 2, curve: true },
  'IPSec ESP':{ stroke: '#f7dc6f', dash: '8,4',   width: 2, curve: true },
  'mTLS':     { stroke: '#fdcb6e', dash: '6,3',   width: 2, curve: true },
  // MPLS — thick orange
  'MPLS':     { stroke: '#ff9800', dash: '',       width: 3, curve: false },
  'LDP':      { stroke: '#ffb74d', dash: '',       width: 3, curve: false },
  'RSVP':     { stroke: '#ff9800', dash: '',       width: 3, curve: false },
  // BGP — blue
  'BGP':      { stroke: '#5dade2', dash: '',       width: 2, curve: false },
  'eBGP':     { stroke: '#3498db', dash: '',       width: 2, curve: false },
  'iBGP':     { stroke: '#85c1e9', dash: '4,4',   width: 2, curve: false },
  'MP-BGP':   { stroke: '#7986cb', dash: '4,4',   width: 2, curve: false },
  'BGP EVPN': { stroke: '#5dade2', dash: '',       width: 2, curve: false },
  // OSPF — green
  'OSPF':     { stroke: '#27ae60', dash: '',       width: 2, curve: false },
  // Optical — purple
  'OC-192':   { stroke: '#9b59b6', dash: '',       width: 3, curve: false },
  'SONET':    { stroke: '#536dfe', dash: '',       width: 3, curve: false },
  'VXLAN':    { stroke: '#00bcd4', dash: '6,3',   width: 2, curve: true },
  // MANET — dotted
  'OLSR':     { stroke: '#9b59b6', dash: '3,3',   width: 2, curve: false },
};

function getLinkStyle(protocol) {
  if (!protocol) return null;
  return LINK_STYLES[protocol] || LINK_STYLES[protocol.toUpperCase()] || null;
}

/* ── Create a link ───────────────────────────────────────────────────────────── */
function createLink(srcId, tgtId, label, protocol, linkId) {
  // Reject orphan endpoints — JointJS otherwise renders an invisible link
  // at origin, which looks like "links sometimes don't appear" in the UI.
  if (!srcId || !tgtId || !graph.getCell(srcId) || !graph.getCell(tgtId)) {
    console.warn('createLink: skipping orphan edge', { srcId: srcId, tgtId: tgtId, linkId: linkId });
    return null;
  }

  const style = getLinkStyle(protocol);
  const stroke = style ? style.stroke : '#e94560';
  const dash = style ? style.dash : '';
  const width = style ? style.width : 2;

  const linkAttrs = {
    line: {
      stroke: stroke,
      strokeWidth: width,
      targetMarker: { type: 'classic', fill: stroke, size: 6 },
    }
  };
  if (dash) {
    linkAttrs.line.strokeDasharray = dash;
  }

  const link = new joint.shapes.standard.Link({
    id: linkId || joint.util.uuid(),
    source: { id: srcId },
    target: { id: tgtId },
    attrs: linkAttrs,
    labels: label ? [{
      attrs: { text: { text: label, fill: '#555', fontSize: 10, fontFamily: 'Cascadia Code, Consolas, monospace' } },
      position: 0.5
    }] : [],
    // Curved routing for tunnels
    ...(style && style.curve ? { connector: { name: 'smooth' } } : {}),
  });
  link.set('protocol', protocol || '');
  graph.addCell(link);
  return link;
}

/* ── Free Connector (standalone link not anchored to nodes) ─────────────────── */
function createFreeConnector(connType, x, y, linkId, lc) {
  const isCurved = connType === 'conn-curved' || (lc && lc.curved);
  const isDashed  = connType === 'conn-dashed'  || (lc && lc.dashed);
  const color = (lc && lc.color) || '#7a8cb0';

  const link = new joint.shapes.standard.Link({
    id: linkId || joint.util.uuid(),
    source: { x: x,       y: y + 20 },
    target: { x: x + 140, y: y + 20 },
    attrs: {
      line: {
        stroke: color,
        strokeWidth: 2,
        strokeDasharray: isDashed ? '8 4' : 'none',
        targetMarker: { type: 'classic', fill: color, size: 6 },
        sourceMarker: { type: 'none' },
      }
    },
    labels: [],
    ...(isCurved ? { connector: { name: 'smooth' } } : { connector: { name: 'normal' } }),
  });

  const config = lc || { freeConn: true, curved: isCurved, dashed: isDashed, color };
  config.freeConn = true;
  link.set('linkConfig', config);
  link.set('protocol', '');
  graph.addCell(link);
  _applyLinkConfig(link, config);
  return link;
}

/* ── Apply full link styling + labels from linkConfig ────────────────────────── */
function _applyLinkConfig(link, lc) {
  if (!link || !lc) return;
  const color = lc.color || link.attr('line/stroke') || '#e94560';
  const labelFill = '#c8d0e0';
  const labelFont = { fontFamily: 'Cascadia Code, Consolas, monospace', fontSize: 10 };

  // Rebuild labels
  const labels = [];
  if (lc.labelStart) labels.push({ attrs: { text: { text: lc.labelStart, fill: labelFill, ...labelFont } }, position: { distance: 0.05, offset: { x: 0, y: -14 } } });
  if (lc.labelMid)   labels.push({ attrs: { text: { text: lc.labelMid,   fill: labelFill, ...labelFont } }, position: 0.5 });
  if (lc.labelEnd)   labels.push({ attrs: { text: { text: lc.labelEnd,   fill: labelFill, ...labelFont } }, position: { distance: 0.95, offset: { x: 0, y: -14 } } });
  link.labels(labels);

  // Color
  link.attr('line/stroke', color);
  link.attr('line/targetMarker/fill', color);

  // Dash
  link.attr('line/strokeDasharray', lc.dashed ? '8 4' : 'none');

  // Curve
  link.set('connector', (lc.curved || lc.curve) ? { name: 'smooth' } : { name: 'normal' });
}

/* ── Drag and Drop from palette ─────────────────────────────────────────────── */
function onDragStart(event) {
  const type = event.target.closest('[data-type]').dataset.type;
  event.dataTransfer.setData('text/plain', type);
  event.dataTransfer.effectAllowed = 'copy';
}

function onDrop(event) {
  event.preventDefault();
  const type = event.dataTransfer.getData('text/plain');
  if (!type) return;

  const canvasRect = document.getElementById('canvas-container').getBoundingClientRect();
  const x = Math.round((event.clientX - canvasRect.left - paper.translate().tx) / (paper.scale().sx / 1) / 10) * 10;
  const y = Math.round((event.clientY - canvasRect.top - paper.translate().ty) / (paper.scale().sy / 1) / 10) * 10;

  pushUndo();

  // Free connectors drop as standalone floating links
  if (type.startsWith('conn-')) {
    const conn = createFreeConnector(type, x, y);
    selectCell(conn);
    markDirty();
    updateStatusBar();
    return;
  }

  const style = getStyle(type);
  const node = createNode(type, x, y, style.label);
  selectCell(node);
  markDirty();
  updateStatusBar();

  // Design Rulebook: fetch context-aware suggestions for this node type
  _fetchDesignSuggestions(type, x, y);
}

/** Fetch design suggestions from rulebook API and show guidance toast. */
async function _fetchDesignSuggestions(nodeType, x, y) {
  try {
    // Gather existing nodes on canvas for context
    const existingNodes = [];
    graph.getElements().forEach(el => {
      const t = el.get('nodeType');
      if (t) {
        const pos = el.position();
        existingNodes.push({
          id: el.id, type: t,
          label: el.attr('label/text') || '',
          x: pos.x, y: pos.y,
        });
      }
    });
    const resp = await fetch(NC_BASE + '/api/design-suggest', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        node_type: nodeType, x: x, y: y,
        existing_nodes: existingNodes,
      }),
    });
    const data = await resp.json();
    if (!data.suggestions || data.suggestions.length === 0) return;
    _showDesignGuidance(data);
  } catch (e) {
    // Guidance is best-effort — don't break canvas on failure
  }
}

/** Display design guidance toast on canvas. */
function _showDesignGuidance(data) {
  // Remove any existing guidance toast
  const old = document.getElementById('design-guidance-toast');
  if (old) old.remove();

  let html = '<div id="design-guidance-toast" class="design-toast">';
  html += '<div class="dt-header"><span class="dt-title">Design Guidance</span>';
  html += '<button class="dt-close" onclick="this.closest(\'.design-toast\').remove()">&times;</button></div>';

  // Suggestions
  if (data.suggestions && data.suggestions.length) {
    html += '<div class="dt-section"><div class="dt-label">Suggestions</div>';
    data.suggestions.forEach(s => {
      html += '<div class="dt-item dt-suggest">' + s + '</div>';
    });
    html += '</div>';
  }

  // Connection suggestions
  if (data.connection_suggestions && data.connection_suggestions.length) {
    html += '<div class="dt-section"><div class="dt-label">Nearby Connections</div>';
    data.connection_suggestions.forEach(c => {
      html += '<div class="dt-item dt-connect">' + c.message +
              ' <span class="dt-meta">(' + c.target_label + ', ' + c.distance + 'px)</span></div>';
    });
    html += '</div>';
  }

  // Warnings
  if (data.warnings && data.warnings.length) {
    html += '<div class="dt-section"><div class="dt-label">Warnings</div>';
    data.warnings.forEach(w => {
      html += '<div class="dt-item dt-warn">' + w.message + '</div>';
    });
    html += '</div>';
  }

  // Checklist summary
  if (data.checklist && Object.keys(data.checklist).length) {
    const required = Object.entries(data.checklist).filter(([k, v]) => v.required);
    if (required.length) {
      html += '<div class="dt-section"><div class="dt-label">Required Config</div>';
      required.forEach(([k, v]) => {
        html += '<div class="dt-item dt-check">' + k.replace(/_/g, ' ') +
                (v.hint ? ' <span class="dt-meta">' + v.hint + '</span>' : '') + '</div>';
      });
      html += '</div>';
    }
  }

  html += '</div>';
  document.body.insertAdjacentHTML('beforeend', html);

  // Auto-dismiss after 15 seconds
  setTimeout(() => {
    const el = document.getElementById('design-guidance-toast');
    if (el) el.remove();
  }, 15000);
}

/* ── Selection & Config Panel ───────────────────────────────────────────────── */
function selectCell(cell) {
  selectedCell = cell;
  _openConfigPanel();

  if (cell.isElement()) {
    const type = cell.get('nodeType') || 'unknown';
    const label = cell.attr('label/text') || '';
    const config = cell.get('configData') || {};

    document.getElementById('config-empty').classList.add('hidden');
    document.getElementById('config-form').classList.remove('hidden');
    const bulkPanel = document.getElementById('bulk-edit-panel');
    if (bulkPanel) bulkPanel.classList.add('hidden');
    const hdr = document.getElementById('config-header-title');
    if (hdr) hdr.textContent = label || type || 'Properties';

    document.getElementById('cfg-type').value = type;
    document.getElementById('cfg-label').value = label;
    document.getElementById('cfg-ip').value = config.ip || '';
    updateSubnetBadge('cfg-ip-subnet-info', config.ip || '');
    // Color pickers
    const fillEl = document.getElementById('cfg-fill-color');
    const textEl = document.getElementById('cfg-text-color');
    if (fillEl) fillEl.value = config._fill || '#1a2a3a';
    if (textEl) textEl.value = config._textColor || '#eaeaea';
    document.getElementById('cfg-protocol').value = config.protocol || '';
    document.getElementById('cfg-mtu').value = config.mtu || '';
    document.getElementById('cfg-vlan').value = config.vlan || '';
    document.getElementById('cfg-vrf').value = config.vrf || '';
    document.getElementById('cfg-asn').value = config.asn || '';
    // BGP
    document.getElementById('cfg-local-pref').value = config.local_pref || '';
    document.getElementById('cfg-med').value = config.med || '';
    document.getElementById('cfg-weight').value = config.weight || '';
    document.getElementById('cfg-community').value = config.community || '';
    // OSPF
    document.getElementById('cfg-ospf-area').value = config.ospf_area || '';
    document.getElementById('cfg-ospf-cost').value = config.ospf_cost || '';
    document.getElementById('cfg-notes').value = config.notes || '';
    // Rotation
    const rot = config._rotation || 0;
    document.getElementById('cfg-rotation').value = rot;
    document.getElementById('cfg-rotation-val').textContent = rot + '\u00B0';
    // Device Info
    document.getElementById('cfg-hostname').value = config.hostname || '';
    const cfgOs = document.getElementById('cfg-os');
    if (cfgOs) cfgOs.value = config.os || '';
    document.getElementById('cfg-model').value = config.model || '';
    document.getElementById('cfg-serial').value = config.serial || '';
    document.getElementById('cfg-asset-tag').value = config.asset_tag || '';
    // IPv6
    document.getElementById('cfg-ipv6').value = config.ipv6 || '';
    document.getElementById('cfg-ipv6-ll').value = config.ipv6_link_local || '';
    document.getElementById('cfg-addr-family').value = config.address_family || '';
    document.getElementById('cfg-ipv6-capable').value = config.ipv6_capable || '';
    // Lifecycle & Tech Debt
    document.getElementById('cfg-install-date').value = config.install_date || '';
    document.getElementById('cfg-eos-date').value = config.eos_date || '';
    document.getElementById('cfg-eol-date').value = config.eol_date || '';
    document.getElementById('cfg-eosup-date').value = config.eosup_date || '';
    document.getElementById('cfg-sw-version').value = config.sw_version || '';
    document.getElementById('cfg-hw-revision').value = config.hw_revision || '';
    // Slot / Port
    document.getElementById('cfg-slot').value = config.slot || '';
    document.getElementById('cfg-port').value = config.port || '';
    document.getElementById('cfg-port-type').value = config.port_type || '';
    document.getElementById('cfg-bandwidth').value = config.bandwidth || '';
    // Location
    document.getElementById('cfg-site').value = config.site || '';
    document.getElementById('cfg-location').value = config.location || '';
    document.getElementById('cfg-rack').value = config.rack || '';
    // Peering
    document.getElementById('cfg-peer-asn').value = config.peer_asn || '';
    document.getElementById('cfg-peer-ip').value = config.peer_ip || '';
    document.getElementById('cfg-peering-type').value = config.peering_type || '';
    // Linked Records
    document.getElementById('cfg-project-id').value = config.project_id || '';
    document.getElementById('cfg-circuit-id').value = config.circuit_id || '';
    document.getElementById('cfg-customer-id').value = config.customer_id || '';
    document.getElementById('cfg-ipam-id').value = config.ipam_block_id || '';
    document.getElementById('cfg-cable-id').value = config.cable_id || '';
    document.getElementById('cfg-xconn-id').value = config.xconn_id || '';
    // Load linked record options
    _loadLinkedRecordOptions(config);

    // Highlight on paper
    paper.findViewByModel(cell)?.highlight();
    // Update rack view if open
    if (typeof renderRackView === 'function') renderRackView();
  } else {
    // Link selected — show link config panel
    document.getElementById('config-empty').classList.add('hidden');
    document.getElementById('config-form').classList.add('hidden');
    const bulkPanel = document.getElementById('bulk-edit-panel');
    if (bulkPanel) bulkPanel.classList.add('hidden');
    const hdr = document.getElementById('config-header-title');
    if (hdr) hdr.textContent = 'Link';
    const linkPanel = document.getElementById('link-config-form');
    if (linkPanel) {
      linkPanel.classList.remove('hidden');
      const lc = cell.get('linkConfig') || {};
      document.getElementById('lcfg-ip').value = lc.ip || '';
      updateSubnetBadge('lcfg-ip-subnet-info', lc.ip || '');
      // Labels
      const lsEl = document.getElementById('lcfg-label-start');
      const lmEl = document.getElementById('lcfg-label-mid');
      const leEl = document.getElementById('lcfg-label-end');
      if (lsEl) lsEl.value = lc.labelStart || '';
      if (lmEl) lmEl.value = lc.labelMid   || '';
      if (leEl) leEl.value = lc.labelEnd    || '';
      // Style
      const lcColorEl = document.getElementById('lcfg-color');
      if (lcColorEl) lcColorEl.value = lc.color || '#e94560';
      const lcCurvedEl = document.getElementById('lcfg-curved');
      if (lcCurvedEl) lcCurvedEl.checked = !!(lc.curved || lc.curve);
      document.getElementById('lcfg-vlan').value = lc.vlan || '';
      document.getElementById('lcfg-vrf').value = lc.vrf || '';
      document.getElementById('lcfg-mtu').value = lc.mtu || '';
      document.getElementById('lcfg-trunk').checked = lc.trunk || false;
      document.getElementById('lcfg-protocol').value = cell.get('protocol') || lc.protocol || '';
    }
  }
}

function updateLinkConfig(key, val) {
  if (!selectedCell || !selectedCell.isLink()) return;
  const lc = selectedCell.get('linkConfig') || {};
  lc[key] = val;
  selectedCell.set('linkConfig', lc);
  if (key === 'protocol') selectedCell.set('protocol', val);
  // Re-apply visual attrs for style-related keys
  if (['labelStart','labelMid','labelEnd','color','curved','dashed'].includes(key)) {
    _applyLinkConfig(selectedCell, lc);
  }
  markDirty();
}

function setLinkColor(color) {
  const el = document.getElementById('lcfg-color');
  if (el) el.value = color;
  updateLinkConfig('color', color);
}

/* ── Subnet Calculator & Host Validator ─────────────────────────────────────── */
function calcSubnet(cidr) {
  const m = cidr.trim().match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\/(\d{1,2})$/);
  if (!m) return null;
  const octets = [+m[1], +m[2], +m[3], +m[4]];
  const prefixLen = +m[5];
  if (octets.some(n => n > 255)) return { valid: false, error: 'Octet out of range (0–255)' };
  if (prefixLen > 32) return { valid: false, error: 'Prefix length must be 0–32' };

  const ipInt = ((octets[0] << 24) | (octets[1] << 16) | (octets[2] << 8) | octets[3]) >>> 0;
  const mask = prefixLen === 0 ? 0 : ((0xFFFFFFFF << (32 - prefixLen)) >>> 0);
  const networkInt = (ipInt & mask) >>> 0;
  const broadcastInt = (networkInt | (~mask >>> 0)) >>> 0;
  const toIP = n => [(n >>> 24) & 0xFF, (n >>> 16) & 0xFF, (n >>> 8) & 0xFF, n & 0xFF].join('.');

  const isP2P  = prefixLen === 31;
  const isHost = prefixLen === 32;
  let isValidHost;
  if (isHost)     isValidHost = true;
  else if (isP2P) isValidHost = (ipInt === networkInt || ipInt === broadcastInt);
  else            isValidHost = ipInt > networkInt && ipInt < broadcastInt;

  const numHosts = isHost ? 1 : isP2P ? 2 : Math.max(0, broadcastInt - networkInt - 1);
  const peer = isP2P ? toIP(ipInt === networkInt ? broadcastInt : networkInt) : null;

  return {
    valid: true,
    ip: toIP(ipInt),
    prefixLen,
    network:    toIP(networkInt),
    broadcast:  isP2P ? null : toIP(broadcastInt),
    hostMin:    toIP(isHost || isP2P ? networkInt : networkInt + 1),
    hostMax:    toIP(isHost || isP2P ? broadcastInt : broadcastInt - 1),
    numHosts,
    isP2P,
    isHost,
    isValidHost,
    peer,
    subnetMask: toIP(mask),
  };
}

function updateSubnetBadge(containerId, cidr) {
  const el = document.getElementById(containerId);
  if (!el) return;
  const trimmed = (cidr || '').trim();
  if (!trimmed || !trimmed.includes('/')) { el.innerHTML = ''; return; }

  const info = calcSubnet(trimmed);
  if (!info) {
    el.innerHTML = '<span class="subnet-badge subnet-badge-error">&#9888; Invalid CIDR format</span>';
    return;
  }
  if (!info.valid) {
    el.innerHTML = `<span class="subnet-badge subnet-badge-error">&#9888; ${info.error}</span>`;
    return;
  }

  const cls = info.isValidHost ? 'subnet-badge-ok' : 'subnet-badge-warn';
  const txt = info.isValidHost ? '&#10003; Valid host address' : '&#10007; Not a valid host address';

  let rows = `<div class="sbi-row"><span class="sbi-lbl">Network</span><span class="sbi-val">${info.network}/${info.prefixLen}</span></div>`;
  rows += `<div class="sbi-row"><span class="sbi-lbl">Subnet Mask</span><span class="sbi-val">${info.subnetMask}</span></div>`;
  if (info.isP2P) {
    rows += `<div class="sbi-row"><span class="sbi-lbl">Type</span><span class="sbi-val sbi-tag-p2p">/31 P2P (RFC 3021)</span></div>`;
    rows += `<div class="sbi-row"><span class="sbi-lbl">Peer IP</span><span class="sbi-val">${info.peer}</span></div>`;
    rows += `<div class="sbi-row"><span class="sbi-lbl">Both usable</span><span class="sbi-val">${info.hostMin} / ${info.hostMax}</span></div>`;
  } else if (info.isHost) {
    rows += `<div class="sbi-row"><span class="sbi-lbl">Type</span><span class="sbi-val">/32 Host Route</span></div>`;
  } else {
    rows += `<div class="sbi-row"><span class="sbi-lbl">Broadcast</span><span class="sbi-val">${info.broadcast}</span></div>`;
    rows += `<div class="sbi-row"><span class="sbi-lbl">Host Range</span><span class="sbi-val">${info.hostMin} &#8211; ${info.hostMax}</span></div>`;
    rows += `<div class="sbi-row"><span class="sbi-lbl">Usable Hosts</span><span class="sbi-val">${info.numHosts.toLocaleString()}</span></div>`;
  }

  el.innerHTML = `<span class="subnet-badge ${cls}">${txt}</span><div class="subnet-info-box">${rows}</div>`;
}

function deselectAll() {
  selectedCell = null;
  _closeConfigPanel();
  document.getElementById('config-empty').classList.remove('hidden');
  document.getElementById('config-form').classList.add('hidden');
  const linkPanel = document.getElementById('link-config-form');
  if (linkPanel) linkPanel.classList.add('hidden');
  const bulkPanel = document.getElementById('bulk-edit-panel');
  if (bulkPanel) bulkPanel.classList.add('hidden');
  const hdr = document.getElementById('config-header-title');
  if (hdr) hdr.textContent = 'Properties';
  updateSubnetBadge('cfg-ip-subnet-info', '');
  updateSubnetBadge('lcfg-ip-subnet-info', '');
  const fillEl = document.getElementById('cfg-fill-color');
  const textEl = document.getElementById('cfg-text-color');
  if (fillEl) fillEl.value = '#1a2a3a';
  if (textEl) textEl.value = '#eaeaea';
}

/* ── Node Color Pickers ──────────────────────────────────────────────────────── */
function applyNodeColor(key, color) {
  if (!selectedCell || !selectedCell.isElement()) return;
  const config = selectedCell.get('configData') || {};
  const type = selectedCell.get('nodeType') || '';
  const style = getStyle(type);

  if (key === 'fill') {
    config._fill = color;
    if (style.shape && ['rect','roundedrect','circle','ellipse','diamond','triangle','hexagon','star'].includes(style.shape)) {
      selectedCell.attr('shape/fill', color);
      if (type === 'callout-bubble') selectedCell.attr('shape/fillOpacity', 1.0);
    } else if (style.shape && ['text','heading','badge'].includes(style.shape)) {
      selectedCell.attr('body/fill', color);
    } else {
      selectedCell.attr('body/fill', color);
    }
  } else if (key === 'text') {
    config._textColor = color;
    selectedCell.attr('label/fill', color);
  }
  selectedCell.set('configData', config);
  markDirty();
}

function setNodeColor(key, color) {
  const pickerId = key === 'fill' ? 'cfg-fill-color' : 'cfg-text-color';
  const el = document.getElementById(pickerId);
  if (el) el.value = color;
  applyNodeColor(key, color);
}

/* ── Annotation Legend ───────────────────────────────────────────────────────── */
var _annotationLegend = [];  // [{id, num, color, text}]
var _annLegendVisible = false;

/* ── Phase Strip State ───────────────────────────────────────────────────────── */
let _phases = [];  // [{topology_id, label, phase_order, active}]

function toggleAnnotationLegend() {
  const panel = document.getElementById('ann-legend-panel');
  if (!panel) return;
  _annLegendVisible = !_annLegendVisible;
  panel.style.display = _annLegendVisible ? 'block' : 'none';
  if (_annLegendVisible) _renderAnnotationLegend();
}

function _renderAnnotationLegend() {
  const container = document.getElementById('ann-legend-entries');
  if (!container) return;
  container.innerHTML = '';
  _annotationLegend.forEach((entry, idx) => {
    const row = document.createElement('div');
    row.className = 'ann-entry';
    row.innerHTML = `
      <div class="ann-bubble" style="background:${entry.color || '#e94560'};" onclick="openCalloutColorPicker(${idx})" title="Click to change color">${entry.num || idx + 1}</div>
      <input type="number" class="ann-num-input" value="${entry.num || idx + 1}" min="1" max="999" oninput="_updateAnnotEntry(${idx}, 'num', +this.value)" title="Callout number"/>
      <input type="color" class="ann-color-input" value="${entry.color || '#e94560'}" oninput="_updateAnnotEntry(${idx}, 'color', this.value)" title="Bubble color"/>
      <input type="text" class="ann-text-input" value="${(entry.text || '').replace(/"/g,'&quot;')}" placeholder="Description…" oninput="_updateAnnotEntry(${idx}, 'text', this.value)"/>
      <button class="ann-del-btn" onclick="_deleteAnnotEntry(${idx})" title="Delete">&times;</button>`;
    container.appendChild(row);
  });
}

function _updateAnnotEntry(idx, key, val) {
  if (!_annotationLegend[idx]) return;
  _annotationLegend[idx][key] = val;
  // Re-render bubble color live
  const bubble = document.querySelectorAll('.ann-bubble')[idx];
  if (bubble && key === 'color') bubble.style.background = val;
  if (bubble && key === 'num') bubble.textContent = val;
  markDirty();
}

function _deleteAnnotEntry(idx) {
  _annotationLegend.splice(idx, 1);
  _renderAnnotationLegend();
  markDirty();
}

function addAnnotLegendEntry() {
  const nextNum = _annotationLegend.length + 1;
  const colors = ['#e94560','#3498db','#2ecc71','#f1c40f','#9b59b6','#e67e22'];
  _annotationLegend.push({ id: joint.util.uuid(), num: nextNum, color: colors[(nextNum - 1) % colors.length], text: '' });
  _renderAnnotationLegend();
  markDirty();
}

function updateSelectedLabel(val) {
  if (selectedCell && selectedCell.isElement()) {
    selectedCell.attr('label/text', val);
    markDirty();
  }
}

function updateConfig(key, val) {
  if (!selectedCell || !selectedCell.isElement()) return;
  const config = selectedCell.get('configData') || {};
  config[key] = val;
  selectedCell.set('configData', config);
  markDirty();
}

/* ── Load linked record dropdown options from API ─────────────────────────── */
async function _loadLinkedRecordOptions(config) {
  const endpoints = {
    'cfg-project-id': {url: NC_BASE + '/api/projects', labelFn: d => d.name, valFn: d => d.id},
    'cfg-circuit-id': {url: NC_BASE + '/api/circuits', labelFn: d => `${d.circuit_id || d.id} — ${d.provider || ''} ${d.bandwidth || ''}`.trim(), valFn: d => d.id},
    'cfg-customer-id': {url: NC_BASE + '/api/customers', labelFn: d => d.name, valFn: d => d.id},
    'cfg-ipam-id': {url: NC_BASE + '/api/ipam', labelFn: d => `${d.block || d.id} (${d.vlan || ''})`.trim(), valFn: d => d.id},
    'cfg-cable-id': {url: NC_BASE + '/api/cables', labelFn: d => `${d.cable_id || d.id} — ${d.cable_type || ''} ${d.length_m ? d.length_m + 'm' : ''}`.trim(), valFn: d => d.id},
    'cfg-xconn-id': {url: NC_BASE + '/api/cross-connects', labelFn: d => `${d.xconn_id || d.id} — ${d.provider || ''}`.trim(), valFn: d => d.id},
  };
  for (const [selectId, ep] of Object.entries(endpoints)) {
    const sel = document.getElementById(selectId);
    if (!sel) continue;
    const configKey = sel.getAttribute('onchange').match(/updateConfig\('([^']+)'/)?.[1] || '';
    const currentVal = config[configKey] || '';
    try {
      const r = await fetch(ep.url);
      const items = await r.json();
      const list = Array.isArray(items) ? items : (items.items || items.data || []);
      sel.innerHTML = '<option value="">— None —</option>';
      list.forEach(d => {
        const opt = document.createElement('option');
        opt.value = ep.valFn(d);
        opt.textContent = ep.labelFn(d);
        if (String(opt.value) === String(currentVal)) opt.selected = true;
        sel.appendChild(opt);
      });
    } catch (e) {
      // API not available — keep empty
    }
  }
}

/* ── Apply saved rotation on node load ─────────────────────────────────────── */
function _applyNodeRotation(node) {
  const config = node.get('configData') || {};
  const deg = parseInt(config._rotation) || 0;
  if (deg === 0) return;
  const type = node.get('nodeType') || '';
  if (type.startsWith('text-')) {
    const size = node.size();
    node.attr('label/transform', `rotate(${deg}, ${size.width/2}, ${size.height/2})`);
  } else if (type.startsWith('draw-')) {
    const lx = node.attr('label/x') || 10;
    const ly = node.attr('label/y') || 18;
    node.attr('label/transform', `rotate(${deg}, ${lx}, ${ly})`);
  } else {
    const size = node.size();
    node.attr('label/transform', `rotate(${deg}, ${size.width/2}, ${size.height-4})`);
  }
}

/* ── Label Rotation ───────────────────────────────────────────────────────── */
function applyRotation(deg) {
  deg = parseInt(deg) || 0;
  document.getElementById('cfg-rotation').value = deg;
  document.getElementById('cfg-rotation-val').textContent = deg + '\u00B0';
  if (!selectedCell || !selectedCell.isElement()) return;
  pushUndo();
  const config = selectedCell.get('configData') || {};
  config._rotation = deg;
  selectedCell.set('configData', config);
  // Apply CSS transform rotation to the label via JointJS attr
  if (deg === 0) {
    selectedCell.attr('label/transform', '');
  } else {
    // Rotate around the label's reference point
    const type = selectedCell.get('nodeType') || '';
    let cx, cy;
    if (type.startsWith('text-')) {
      // Text nodes: rotate around center
      const size = selectedCell.size();
      cx = size.width / 2;
      cy = size.height / 2;
      selectedCell.attr('label/transform', `rotate(${deg}, ${cx}, ${cy})`);
    } else if (type.startsWith('draw-')) {
      // Drawing shapes: rotate label around its position
      const labelX = selectedCell.attr('label/x') || 10;
      const labelY = selectedCell.attr('label/y') || 18;
      selectedCell.attr('label/transform', `rotate(${deg}, ${labelX}, ${labelY})`);
    } else {
      // Network devices: rotate around label anchor (center-bottom)
      const size = selectedCell.size();
      cx = size.width / 2;
      cy = size.height - 4;
      selectedCell.attr('label/transform', `rotate(${deg}, ${cx}, ${cy})`);
    }
  }
  markDirty();
}

function deleteSelected() {
  if (!selectedCell) return;
  pushUndo();
  selectedCell.remove();
  deselectAll();
  updateStatusBar();
  markDirty();
}

/* ── Color Palette — apply fill/stroke/text colors ────────────────────────── */
const COLOR_PRESETS = [
  '#e94560', '#e74c3c', '#ff6b6b', '#f39c12', '#f1c40f', '#fdcb6e',
  '#27ae60', '#2ecc71', '#00b894', '#3498db', '#4a9eff', '#74b9ff',
  '#9b59b6', '#a29bfe', '#6c5ce7', '#0f3460', '#1a2a3a', '#1a1a2e',
  '#2d3436', '#636e72', '#b2bec3', '#dfe6e9', '#ffffff', '#000000',
];

function applyFillColor(color) {
  if (!selectedCell || !selectedCell.isElement()) return;
  pushUndo();
  const config = selectedCell.get('configData') || {};
  config._fill = color;
  selectedCell.set('configData', config);
  const type = selectedCell.get('nodeType') || '';
  if (type.startsWith('draw-')) {
    selectedCell.attr('shape/fill', color);
  } else if (type.startsWith('text-')) {
    selectedCell.attr('body/fill', color);
  } else {
    // Cisco stencil — fill is on stencilBody
    selectedCell.attr('stencilBody/fill', color);
  }
  markDirty();
}

function applyStrokeColor(color) {
  if (!selectedCell || !selectedCell.isElement()) return;
  pushUndo();
  const config = selectedCell.get('configData') || {};
  config._stroke = color;
  selectedCell.set('configData', config);
  const type = selectedCell.get('nodeType') || '';
  if (type.startsWith('draw-')) {
    selectedCell.attr('shape/stroke', color);
  } else if (type.startsWith('text-')) {
    selectedCell.attr('body/stroke', color);
  } else {
    // Cisco stencil — detail stroke color
    selectedCell.attr('stencilDetail/stroke', color);
  }
  markDirty();
}

function applyTextColor(color) {
  if (!selectedCell || !selectedCell.isElement()) return;
  pushUndo();
  const config = selectedCell.get('configData') || {};
  config._textColor = color;
  selectedCell.set('configData', config);
  selectedCell.attr('label/fill', color);
  markDirty();
}

function renderColorSwatches(containerId, callback) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = '';
  COLOR_PRESETS.forEach(c => {
    const swatch = document.createElement('span');
    swatch.className = 'color-swatch';
    swatch.style.background = c;
    if (c === '#ffffff') swatch.style.border = '1px solid #636e72';
    swatch.title = c;
    swatch.onclick = () => callback(c);
    container.appendChild(swatch);
  });
  // Custom color input
  const custom = document.createElement('input');
  custom.type = 'color';
  custom.className = 'color-swatch-custom';
  custom.title = 'Custom color';
  custom.value = '#4a9eff';
  custom.onchange = () => callback(custom.value);
  container.appendChild(custom);
}

function initColorPalettes() {
  renderColorSwatches('fill-colors', applyFillColor);
  renderColorSwatches('stroke-colors', applyStrokeColor);
  renderColorSwatches('text-colors', applyTextColor);
}

/* ── Undo / Redo ─────────────────────────────────────────────────────────────── */
function pushUndo() {
  undoStack.push(graphToJSON());
  redoStack = [];
}

function undoAction() {
  if (!undoStack.length) return;
  redoStack.push(graphToJSON());
  const prev = undoStack.pop();
  loadGraphJSON(prev);
  deselectAll();
  updateStatusBar();
}

function redoAction() {
  if (!redoStack.length) return;
  undoStack.push(graphToJSON());
  const next = redoStack.pop();
  loadGraphJSON(next);
  deselectAll();
  updateStatusBar();
}

/* ── Serialization ────────────────────────────────────────────────────────────── */
function graphToJSON() {
  const cells = graph.getCells();
  const nodes = [];
  const edges = [];

  cells.forEach(cell => {
    if (cell.isElement()) {
      const pos = cell.position();
      nodes.push({
        id: cell.id,
        label: cell.attr('label/text') || '',
        type: cell.get('nodeType') || 'unknown',
        x: pos.x,
        y: pos.y,
        config: cell.get('configData') || {}
      });
    } else if (cell.isLink()) {
      const src = cell.get('source');
      const tgt = cell.get('target');
      const lc  = cell.get('linkConfig') || {};
      const edgeObj = {
        id: cell.id,
        source: src.id || null,
        target: tgt.id || null,
        label: cell.labels().length ? (cell.labels()[0].attrs?.text?.text || '') : '',
        protocol: cell.get('protocol') || '',
      };
      if (lc.freeConn) {
        edgeObj.freeConn = true;
        edgeObj.srcX = (src.x != null ? src.x : (src.point ? src.point.x : 0));
        edgeObj.srcY = (src.y != null ? src.y : (src.point ? src.point.y : 0));
        edgeObj.tgtX = (tgt.x != null ? tgt.x : (tgt.point ? tgt.point.x : 140));
        edgeObj.tgtY = (tgt.y != null ? tgt.y : (tgt.point ? tgt.point.y : 0));
      }
      const cableData = cell.get('cableData');
      if (cableData && Object.keys(cableData).length) edgeObj.cableData = cableData;
      if (Object.keys(lc).length) edgeObj.config = lc;
      edges.push(edgeObj);
    }
  });

  return { nodes, edges, _annotationLegend: _annotationLegend, _phases: _phases, _aiCorrections: _aiCorrections.slice() };
}

function _resolveNodeOverlaps(g, padding) {
  padding = padding == null ? 20 : padding;
  const els = g.getElements();
  if (els.length < 2) return;
  for (let iter = 0; iter < 80; iter++) {
    let moved = false;
    for (let i = 0; i < els.length; i++) {
      for (let j = i + 1; j < els.length; j++) {
        const a = els[i], b = els[j];
        const ap = a.position(), as_ = a.size();
        const bp = b.position(), bs = b.size();
        const p2 = padding / 2;
        const ax1 = ap.x - p2, ay1 = ap.y - p2, ax2 = ap.x + as_.width + p2, ay2 = ap.y + as_.height + p2;
        const bx1 = bp.x - p2, by1 = bp.y - p2, bx2 = bp.x + bs.width + p2, by2 = bp.y + bs.height + p2;
        if (ax2 <= bx1 || bx2 <= ax1 || ay2 <= by1 || by2 <= ay1) continue;
        let dx = (bx1 + bx2) / 2 - (ax1 + ax2) / 2;
        let dy = (by1 + by2) / 2 - (ay1 + ay2) / 2;
        if (dx === 0 && dy === 0) { dx = 1; dy = 0; }
        const ovX = (ax2 - ax1) / 2 + (bx2 - bx1) / 2 - Math.abs(dx);
        const ovY = (ay2 - ay1) / 2 + (by2 - by1) / 2 - Math.abs(dy);
        if (ovX <= 0 || ovY <= 0) continue;
        let px = 0, py = 0;
        if (ovX <= ovY) { px = (ovX / 2 + 0.5) * (dx >= 0 ? 1 : -1); }
        else            { py = (ovY / 2 + 0.5) * (dy >= 0 ? 1 : -1); }
        a.position(ap.x - px, ap.y - py);
        b.position(bp.x + px, bp.y + py);
        moved = true;
      }
    }
    if (!moved) break;
  }
}

function loadGraphJSON(data) {
  graph.clear();
  const nodes = data.nodes || [];
  const edges = data.edges || [];

  // Restore annotation legend
  _annotationLegend = Array.isArray(data._annotationLegend) ? data._annotationLegend : [];

  // Restore phase strip state
  _phases = Array.isArray(data._phases) ? data._phases : [];
  renderPhaseStrip();

  const createdIds = new Set();
  nodes.forEach(n => {
    const node = createNode(n.type, n.x, n.y, n.label, n.id, n.config);
    if (node) createdIds.add(node.id);
  });

  let dropped = 0;
  edges.forEach(e => {
    // Free (floating) connectors — no node endpoints
    if (e.freeConn || (e.config && e.config.freeConn)) {
      const lc = e.config || {};
      const connType = lc.dashed ? 'conn-dashed' : lc.curved ? 'conn-curved' : 'conn-straight';
      const link = createFreeConnector(connType, e.srcX || 100, e.srcY || 100, e.id, lc);
      if (link) {
        link.set('source', { x: e.srcX || 100, y: e.srcY || 100 });
        link.set('target', { x: e.tgtX || 240, y: e.tgtY || 100 });
        _applyLinkConfig(link, lc);
      }
      return;
    }
    if (!e.source || !e.target) { dropped++; return; }
    if (!createdIds.has(e.source) || !createdIds.has(e.target)) {
      dropped++;
      console.warn('loadGraphJSON: dropping edge with missing endpoint', e);
      return;
    }
    const link = createLink(e.source, e.target, e.label, e.protocol, e.id);
    if (link) {
      if (e.cableData) link.set('cableData', e.cableData);
      if (e.config) {
        link.set('linkConfig', e.config);
        _applyLinkConfig(link, e.config);
      }
    }
  });
  if (dropped > 0) {
    console.warn('loadGraphJSON: dropped ' + dropped + ' edge(s) referencing missing nodes');
  }
  _resolveNodeOverlaps(graph, 20);
  updateStatusBar();
  requestAnimationFrame(() => {
    graph.getElements().forEach(cell => _applyAiCorrectedGlow(cell));
  });
}

/* ── Phase Strip Renderer ─────────────────────────────────────────────────────── */
function renderPhaseStrip() {
  const strip = document.getElementById('phase-nav-strip');
  if (!strip) return;
  if (!_phases.length) {
    strip.classList.remove('pns-visible');
    strip.innerHTML = '';
    return;
  }
  const PHASE_LABELS = { 'as-is': 'As-Is', 'future': 'Future', 'logical': 'Logical' };
  const sorted = _phases.slice().sort((a, b) => (a.phase_order || 0) - (b.phase_order || 0));
  const parts = [];
  sorted.forEach((p, i) => {
    if (i > 0) parts.push('<span class="pns-arrow">›</span>');
    const display = PHASE_LABELS[p.label] || p.label;
    if (p.active) {
      parts.push(`<span class="pns-pill pns-active">${display}</span>`);
    } else {
      const href = `${NC_BASE}/canvas/${encodeURIComponent(p.topology_id)}`;
      parts.push(`<a href="${href}" class="pns-pill">${display}</a>`);
    }
  });
  strip.innerHTML = parts.join('');
  strip.classList.add('pns-visible');
}

/* ── API: Save ────────────────────────────────────────────────────────────────── */
async function saveTopology() {
  const gj = graphToJSON();
  const name = document.getElementById('topo-name-display').textContent.trim();
  setStatus('Saving...');

  try {
    if (!currentTopoId || currentTopoId === 'new') {
      // Create new
      const r = await fetch(NC_BASE + '/api/topologies', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, graph_json: gj })
      });
      const data = await r.json();
      if (data.id) {
        currentTopoId = data.id;
        window.history.replaceState({}, '', NC_BASE + `/canvas/${data.id}`);
      }
    } else {
      const putResp = await fetch(NC_BASE + `/api/topologies/${currentTopoId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ graph_json: gj })
      });
      const putData = await putResp.json().catch(() => ({}));
      if (putData.sdc_assessment) {
        _showSdcToast(putData.sdc_assessment, 'NDC');
      }
    }
    isDirty = false;
    const now = new Date().toLocaleTimeString();
    document.getElementById('sb-saved').textContent = `Saved at ${now}`;
    setStatus('Saved');
  } catch (err) {
    setStatus('Save failed: ' + err.message);
  }
}

function _showSdcToast(sdc, source) {
  const cat1 = sdc.cat1_count || 0;
  const grade = sdc.posture_grade || '?';
  const score = sdc.risk_score != null ? sdc.risk_score.toFixed(1) : '—';
  const gradeColor = grade <= 'B' ? '#27ae60' : grade === 'C' ? '#f39c12' : '#e74c3c';
  const cat1Text = cat1 > 0
    ? `<span style="color:#e74c3c;font-weight:bold;">&#9888; ${cat1} CAT1</span>`
    : `<span style="color:#27ae60;">&#10003; 0 CAT1</span>`;

  const existing = document.getElementById('sdc-cross-toast');
  if (existing) existing.remove();

  const toast = document.createElement('div');
  toast.id = 'sdc-cross-toast';
  toast.style.cssText = [
    'position:fixed', 'bottom:24px', 'right:24px', 'z-index:9999',
    'background:#0f1e35', 'border:1px solid ' + (cat1 > 0 ? '#c0392b' : '#1e3a6e'),
    'border-radius:8px', 'padding:12px 16px', 'min-width:260px',
    'box-shadow:0 4px 16px rgba(0,0,0,0.5)', 'font-family:sans-serif',
  ].join(';');
  toast.innerHTML = `
    <div style="font-size:11px;color:#7a8cb0;margin-bottom:4px;">SDC Re-assessed (triggered by ${source} save)</div>
    <div style="display:flex;align-items:center;gap:12px;">
      <div style="font-size:2rem;font-weight:bold;color:${gradeColor};">${grade}</div>
      <div>
        <div style="font-size:13px;color:#eaeaea;">Score: ${score}</div>
        <div style="font-size:13px;">${cat1Text}</div>
      </div>
    </div>
    ${cat1 > 0 ? '<div style="margin-top:8px;font-size:11px;color:#e74c3c;">View findings on <a href="/security/posture" style="color:#e74c3c;text-decoration:underline;">Security Posture</a></div>' : ''}
    <button onclick="this.parentElement.remove()" style="position:absolute;top:8px;right:8px;background:none;border:none;color:#7a8cb0;cursor:pointer;font-size:14px;">&#10005;</button>
  `;
  document.body.appendChild(toast);
  setTimeout(() => { if (toast.parentElement) toast.remove(); }, 8000);
}

/* ── API: Load ────────────────────────────────────────────────────────────────── */
async function loadTopology(id) {
  setStatus('Loading...');
  try {
    const r = await fetch(NC_BASE + `/api/topologies/${id}`);
    if (!r.ok) throw new Error('Not found');
    const data = await r.json();
    if (data.graph_json) {
      loadGraphJSON(data.graph_json);
      if ((data.graph_json.nodes || []).length > 0) {
        requestAnimationFrame(() => setTimeout(() => { if (typeof zoomFit === 'function') zoomFit(); }, 100));
      }
    }
    document.getElementById('topo-name-display').textContent = data.name || 'Untitled';
    setStatus('Loaded — ' + data.name);
    isDirty = false;
  } catch (err) {
    setStatus('Load failed: ' + err.message);
  }
}

/* ── Export ───────────────────────────────────────────────────────────────────── */
async function exportAs(fmt) {
  if (!_conflictExportBypassed && Object.keys(_conflicts || {}).length > 0) {
    _guardExport(fmt.toUpperCase() + ' export', () => exportAs(fmt)); return;
  }
  if (!currentTopoId || currentTopoId === 'new') {
    await saveTopology();
  }
  if (!currentTopoId || currentTopoId === 'new') {
    alert('Save the topology first before exporting.');
    return;
  }
  // VSDX and CSV exports return binary ZIP — use dedicated endpoints
  if (fmt === 'vsdx' || fmt === 'csv') {
    const endpoint = fmt === 'vsdx'
      ? NC_BASE + `/api/export/${currentTopoId}/vsdx`
      : NC_BASE + `/api/export/${currentTopoId}/csv`;
    try {
      const r = await fetch(endpoint, { method: 'POST' });
      const data = await r.json();
      if (data.content_b64) {
        const bin = atob(data.content_b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const blob = new Blob([bytes], { type: 'application/octet-stream' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = data.filename;
        a.click();
      } else if (data.error) {
        alert('Export error: ' + data.error);
      }
    } catch (e) {
      alert('Export failed: ' + e.message);
    }
    return;
  }
  const r = await fetch(NC_BASE + `/api/export/${currentTopoId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ format: fmt })
  });
  const data = await r.json();
  if (data.content) {
    const blob = new Blob([data.content], { type: 'text/plain' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = data.filename;
    a.click();
  }
}

/* ── Templates Panel ─────────────────────────────────────────────────────────── */
async function openTemplatesPanel() {
  const overlay = document.getElementById('tpl-overlay');
  overlay.classList.remove('hidden');
  const list = document.getElementById('tpl-list');
  list.innerHTML = 'Loading...';

  const r = await fetch(NC_BASE + '/api/templates');
  const tpls = await r.json();
  list.innerHTML = '';
  tpls.forEach(t => {
    const div = document.createElement('div');
    div.className = 'tpl-list-item';
    div.innerHTML = `
      <div>
        <div class="tpl-list-cat">${t.category}</div>
        <div class="tpl-list-name">${t.name}</div>
      </div>
      <button class="btn btn-sm btn-primary" onclick="loadTemplateIntoCanvas('${t.id}')">Load</button>
    `;
    list.appendChild(div);
  });
}

function closeTplPanel() {
  document.getElementById('tpl-overlay').classList.add('hidden');
}

async function loadTemplateIntoCanvas(tplId) {
  const r = await fetch(NC_BASE + `/api/templates/${tplId}`);
  const tpl = await r.json();
  if (!tpl.graph_json) return;
  pushUndo();
  loadGraphJSON(tpl.graph_json);
  document.getElementById('topo-name-display').textContent = tpl.name + ' (copy)';
  closeTplPanel();
  markDirty();
  setStatus('Template loaded: ' + tpl.name);
}

/* ── Enclave-in-a-Box Snippets ────────────────────────────────────────────────── */

const SNIPPET_CLASSIFICATION_COLORS = {
  'SECRET': '#e74c3c',
  'TOP SECRET': '#8e44ad',
  'CUI': '#f39c12',
  'PUBLIC': '#27ae60',
};

async function openSnippetsPanel() {
  const overlay = document.getElementById('snippets-overlay');
  overlay.classList.remove('hidden');
  const list = document.getElementById('snippets-list');
  list.innerHTML = '<div style="color:var(--text-dim);font-size:12px;text-align:center;padding:20px;">Loading snippets…</div>';

  try {
    const r = await fetch(NC_BASE + '/api/snippets');
    const snippets = await r.json();
    list.innerHTML = '';
    if (!snippets.length) {
      list.innerHTML = '<div style="color:var(--text-dim);font-size:12px;text-align:center;padding:20px;">No snippets found.</div>';
      return;
    }
    snippets.forEach(s => {
      const clColor = SNIPPET_CLASSIFICATION_COLORS[s.classification_level] || '#7a8cb0';
      const stigBadges = (s.stig_controls || []).slice(0, 6).map(c =>
        `<span style="display:inline-block;background:rgba(127,140,176,0.15);border:1px solid rgba(127,140,176,0.3);border-radius:3px;padding:1px 5px;font-size:10px;margin:1px;">${c}</span>`
      ).join('');
      const card = document.createElement('div');
      card.style.cssText = 'background:var(--card-bg,#16213e);border:1px solid var(--border,#2a3a5e);border-radius:6px;padding:12px;';
      card.innerHTML = `
        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px;margin-bottom:6px;">
          <div>
            <div style="font-size:13px;font-weight:600;color:var(--text,#eaeaea);">&#x229e; ${s.name}</div>
            <div style="font-size:10px;color:var(--text-dim,#7a8cb0);margin-top:2px;">${s.category} &bull; Impact Level: ${s.impact_level}</div>
          </div>
          <span style="flex-shrink:0;font-size:10px;font-weight:700;color:${clColor};border:1px solid ${clColor};border-radius:3px;padding:2px 6px;white-space:nowrap;">${s.classification_level}</span>
        </div>
        <div style="font-size:11px;color:var(--text-dim,#7a8cb0);margin-bottom:8px;line-height:1.5;">${s.description || ''}</div>
        <div style="margin-bottom:8px;">${stigBadges}</div>
        <button class="btn btn-sm btn-primary" style="width:100%;background:#5c42d9;border-color:#5c42d9;"
          onclick="insertSnippetOntoCanvas('${s.id}', this)">&#x229e; Insert onto Canvas</button>
      `;
      list.appendChild(card);
    });
  } catch (err) {
    list.innerHTML = `<div style="color:#e74c3c;font-size:12px;text-align:center;padding:20px;">Error loading snippets: ${err.message}</div>`;
  }
}

function closeSnippetsPanel() {
  document.getElementById('snippets-overlay').classList.add('hidden');
}

/* ── Integration Guide — classification-aware connection suggestions ─────── */
// Security classification hierarchy (higher number = more restrictive)
const CLASSIFICATION_RANK = {
  'PUBLIC': 0, 'UNCLASSIFIED': 0, 'public': 0, 'unclassified': 0,
  'CUI': 1, 'cui': 1, 'internal': 1,
  'SECRET': 2, 'secret': 2,
  'TOP SECRET': 3, 'top secret': 3, 'TS': 3, 'ts': 3,
};

// Node types by security role
const TYPE_SETS = {
  WAN_UNTRUSTED: new Set([
    'cloud', 'aws-dx', 'aws-dx-gw', 'az-er', 'az-er-global', 'gcp-ic',
    'oci-fc', 'ibm-dl', 'aws-vpn', 'az-vpn-gw', 'gcp-vpn', 'ibm-vpn',
    'pop', 'mpls-pe', 'mpls-p', 'sdwan-edge',
  ]),
  FIREWALL: new Set([
    'firewall', 'aws-nfw', 'az-fw', 'gcp-armor', 'oci-waf', 'aws-waf',
    'az-nsg', 'oci-nsg',
  ]),
  TYPE1_ENCRYPTOR: new Set([
    'type1-encryptor', 'kg-175d', 'kg-175g', 'kg-250', 'kg-340', 'kg-245x', 'kg-255',
    'dod-type1-encryptor',
  ]),
  FIPS_ENCRYPTOR: new Set([
    'fips-140-l1', 'fips-140-l2', 'fips-140-l3', 'fips-140-l4',
    'hsm', 'macsec', 'tls-terminator',
  ]),
  ROUTING: new Set(['router', 'switch-l3', 'route-reflector']),
  SWITCHING: new Set(['switch-l2', 'switch-l3']),
  SERVER: new Set(['server']),
  MONITORING: new Set(['siem', 'network-tap']),
};

/* ── Physical-to-Logical element mapping (ndc-pl-01) ────────────────────── */

// Maps physical node types to their logical canvas equivalents.
// Used by the P→L view transform to replace physical nodes with logical overlays.
const PHYS_TO_LOGICAL = {
  'router':          'vrf',
  'switch-l3':       'vrf',
  'switch-l2':       'vlan',
  'firewall':        'security-zone',
  'server':          'annotation',
  'load-balancer':   'annotation',
  'wap':             'annotation',
  'wlc':             'annotation',
  'cloud':           'annotation',
  'sdwan-edge':      'vrf',
  'route-reflector': 'vrf',
  'aws-vpc':         'vrf',
  'az-vnet':         'vrf',
  'gcp-vpc':         'vrf',
  'oci-vcn':         'vrf',
  'type1-encryptor':        'security-zone',
  'fips-140-l1':            'security-zone',
  'fips-140-l2':            'security-zone',
  'fips-140-l3':            'security-zone',
  'fips-140-l4':            'security-zone',
  'hsm':                    'security-zone',
  // DoD classified types
  'dod-jwics-backbone':     'vrf',
  'dod-jwics-gateway':      'vrf',
  'dod-jwics-dns':          'security-zone',
  'dod-jwics-mail-relay':   'security-zone',
  'dod-type1-encryptor':    'security-zone',
  'dod-scif-lan':           'security-zone',
  'dod-c2s-direct-connect': 'vrf',
  'dod-c2s-tgw':            'vrf',
  'dod-c2s-vpc':            'vrf',
  'dod-c2s-dns-phz':        'security-zone',
  'dod-c2e-expressroute':   'vrf',
  'dod-c2e-vnet':           'vrf',
  'dod-c2e-dns-private':    'security-zone',
  'dod-secret-bcap':        'security-zone',
  'dod-cds':                'security-zone',
};

// Physical-layer types dropped when rendering the logical view (no logical equivalent).
const LOGICAL_DROP_TYPES = new Set([
  'media-ge', 'media-10ge', 'media-25ge', 'media-40ge', 'media-100ge', 'media-400ge',
  'sfp', 'sfp-plus', 'qsfp', 'qsfp-dd',
  'patch-panel',
  'media-fiber', 'media-optical', 'media-converter',
  'roadm', 'oadm', 'edfa', 'transponder', 'olt', 'odf',
]);

// Merge rules applied after P→L substitution.
// Each rule: { match(a,b)→bool, into: logicalType, label(a)→string }
// Linked nodes satisfying match() are collapsed into one logical node.
const LOGICAL_MERGE_RULES = [
  {
    // Adjacent switch-l2 nodes with the same vlanId → single VLAN node
    match: (a, b) => a.nodeType === 'switch-l2' && b.nodeType === 'switch-l2'
                  && a.vlanId != null && a.vlanId === b.vlanId,
    into:  'vlan',
    label: (a) => `VLAN ${a.vlanId}`,
  },
  {
    // Adjacent switch-l3/router nodes with the same vrfName → single VRF node
    match: (a, b) => ['switch-l3', 'router'].includes(a.nodeType)
                  && ['switch-l3', 'router'].includes(b.nodeType)
                  && a.vrfName && a.vrfName === b.vrfName,
    into:  'vrf',
    label: (a) => `VRF ${a.vrfName}`,
  },
];

/* ── Derive Logical Topology (ndc-pl-02) ─────────────────────────────────── */

// Generate a logical graph JSON from the current physical canvas, post it as a
// new topology, and return the new topology ID.
//
// Algorithm:
//   1. Snapshot current graph via graphToJSON()
//   2. Drop nodes in LOGICAL_DROP_TYPES; map surviving node types via PHYS_TO_LOGICAL
//   3. Apply LOGICAL_MERGE_RULES: union-find over adjacent node pairs that satisfy
//      a rule's match() — merged groups collapse to one logical node (averaged pos)
//   4. Rebuild edges between surviving logical nodes; drop self-loops and duplicates
//   5. Attach _meta.derivedFrom = currentTopoId
//   6. POST to /api/topologies → return new topology ID
async function deriveLogicalTopology() {
  const physGraph = graphToJSON();
  const physNodes = physGraph.nodes || [];
  const physEdges = physGraph.edges || [];

  // Step 1 — filter and map nodes
  const surviving = [];
  physNodes.forEach(n => {
    if (LOGICAL_DROP_TYPES.has(n.type)) return;
    const cfg = n.config || {};
    surviving.push({
      id:       n.id,
      label:    n.label,
      type:     PHYS_TO_LOGICAL[n.type] || n.type,
      nodeType: n.type,   // original physical type — used by merge rule match()
      vlanId:   cfg.vlan_id  ?? cfg.vlanId  ?? null,
      vrfName:  cfg.vrf_name ?? cfg.vrfName ?? null,
      x: n.x,
      y: n.y,
      config: cfg,
    });
  });

  const survivingIds = new Set(surviving.map(n => n.id));
  const nodeById = Object.fromEntries(surviving.map(n => [n.id, n]));

  // Step 2 — union-find for merge groups
  const parent = Object.fromEntries(surviving.map(n => [n.id, n.id]));
  function find(x) {
    while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; }
    return x;
  }
  function union(a, b) { parent[find(a)] = find(b); }

  physEdges.forEach(e => {
    if (!survivingIds.has(e.source) || !survivingIds.has(e.target)) return;
    const a = nodeById[e.source];
    const b = nodeById[e.target];
    LOGICAL_MERGE_RULES.forEach(rule => {
      if (rule.match(a, b)) union(a.id, b.id);
    });
  });

  // Step 3 — build one logical node per union group
  const groups = {};
  surviving.forEach(n => {
    const rep = find(n.id);
    if (!groups[rep]) groups[rep] = [];
    groups[rep].push(n);
  });

  const mergedIdMap = {};  // physId → representative logical node id
  const logicalNodes = [];

  Object.entries(groups).forEach(([rep, members]) => {
    const repr = members[0];
    let nodeType = repr.type;
    let label    = repr.label;

    if (members.length > 1) {
      for (const rule of LOGICAL_MERGE_RULES) {
        if (members.length >= 2 && rule.match(repr, members[1])) {
          nodeType = rule.into;
          label    = rule.label(repr);
          break;
        }
      }
    }

    const avgX = members.reduce((s, n) => s + n.x, 0) / members.length;
    const avgY = members.reduce((s, n) => s + n.y, 0) / members.length;

    logicalNodes.push({ id: rep, label, type: nodeType, x: avgX, y: avgY, config: repr.config });
    members.forEach(n => { mergedIdMap[n.id] = rep; });
  });

  // Step 4 — rebuild edges (deduplicated, no self-loops)
  const seenEdges = new Set();
  const logicalEdges = [];
  physEdges.forEach(e => {
    const src = mergedIdMap[e.source];
    const tgt = mergedIdMap[e.target];
    if (!src || !tgt || src === tgt) return;
    const key = [src, tgt].sort().join('::');
    if (seenEdges.has(key)) return;
    seenEdges.add(key);
    logicalEdges.push({ id: e.id + '-log', source: src, target: tgt, label: e.label || '', protocol: e.protocol || '' });
  });

  // Step 5 — assemble derived graph with _meta
  const derivedGraph = {
    nodes: logicalNodes,
    edges: logicalEdges,
    _meta: {
      derivedFrom:  currentTopoId,
      derivedAt:    new Date().toISOString(),
      type:         'logical',
      physNodeCount: physNodes.length,
      logNodeCount:  logicalNodes.length,
    },
  };

  // Step 6 — POST to /api/topologies
  const srcName = document.getElementById('topo-name-display')?.textContent?.trim() || 'Topology';
  const r = await fetch(NC_BASE + '/api/topologies', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: srcName + ' — Logical View', graph_json: derivedGraph }),
  });
  if (!r.ok) throw new Error('deriveLogicalTopology: POST failed (' + r.status + ')');
  const data = await r.json();
  return data.id;
}

// UI handler: derive logical topology and navigate to the new canvas
async function deriveLogicalTopologyUI() {
  if (!currentTopoId || currentTopoId === 'new') {
    setStatus('Save topology first before deriving logical view.');
    return;
  }
  const btn = document.getElementById('tb-derive-logical-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Deriving…'; }
  try {
    const newId = await deriveLogicalTopology();
    window.open(NC_BASE + '/canvas/' + encodeURIComponent(newId), '_blank');
    setStatus('Logical topology created → opening in new tab');
  } catch (err) {
    setStatus('Derive logical failed: ' + err.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '⬡ Derive Logical'; }
  }
}

// ── ndc-pl-03: To Logical toolbar button ─────────────────────────────────────

/** Generic canvas toast. type: 'success' | 'warning' | 'error' */
function ncShowToast(msg, type) {
  const id = 'nc-generic-toast';
  const old = document.getElementById(id);
  if (old) old.remove();
  const color = type === 'error' ? '#e74c3c' : type === 'warning' ? '#f39c12' : '#27ae60';
  const icon  = type === 'error' ? '✕' : '✓';
  const t = document.createElement('div');
  t.id = id;
  t.style.cssText = [
    'position:fixed', 'bottom:24px', 'right:24px', 'z-index:9999',
    'background:#0f1e35', 'border:1px solid ' + color,
    'border-radius:8px', 'padding:12px 18px', 'min-width:260px',
    'box-shadow:0 4px 16px rgba(0,0,0,0.5)',
    'font-family:sans-serif', 'font-size:13px', 'color:#eaeaea',
  ].join(';');
  t.innerHTML = '<span style="color:' + color + ';margin-right:8px;">' + icon + '</span>' + msg +
    '<button onclick="this.parentElement.remove()" style="position:absolute;top:6px;right:8px;background:none;border:none;color:#7a8cb0;cursor:pointer;font-size:14px;">\xd7</button>';
  document.body.appendChild(t);
  setTimeout(() => { if (t.parentElement) t.remove(); }, 6000);
}

/** Enable/disable the To Logical button based on node count (need ≥ 3). */
function _updateToLogicalBtnState() {
  const btn = document.getElementById('tb-to-logical-btn');
  if (!btn) return;
  const n = graph.getElements().length;
  btn.disabled = n < 3;
  btn.title = n < 3
    ? 'To Logical — need at least 3 nodes on the canvas'
    : 'To Logical — collapse physical nodes to logical overlays (VRF/VLAN/Zone) and open as new topology';
}

/** Full UI handler for the To Logical toolbar button. */
async function toLogicalUI() {
  if (!currentTopoId || currentTopoId === 'new') {
    ncShowToast('Save the topology first before deriving the logical view.', 'warning');
    return;
  }
  const btn = document.getElementById('tb-to-logical-btn');
  const origHTML = btn ? btn.innerHTML : '';
  if (btn) { btn.disabled = true; btn.innerHTML = '&#x23f3; Deriving&hellip;'; }
  try {
    // 1. Build and POST the logical topology
    const newId = await deriveLogicalTopology();

    // 2. Patch source topology: add phase link → logical view
    const srcResp = await fetch(NC_BASE + '/api/topologies/' + currentTopoId);
    if (srcResp.ok) {
      const srcData   = await srcResp.json();
      const srcGj     = srcData.graph_json || { nodes: [], edges: [] };
      const srcPhases = Array.isArray(srcGj._phases) ? srcGj._phases : [];
      if (!srcPhases.some(p => p.topology_id === newId)) {
        const nextOrder = srcPhases.reduce((m, p) => Math.max(m, p.phase_order || 0), 0) + 1;
        srcPhases.push({ topology_id: newId, label: 'logical', phase_order: nextOrder, active: false });
      }
      srcGj._phases = srcPhases;
      await fetch(NC_BASE + '/api/topologies/' + currentTopoId, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ graph_json: srcGj }),
      });
      // Reflect phase link locally so the strip updates immediately
      _phases = srcPhases.map(p =>
        p.topology_id === currentTopoId ? { ...p, active: true } : { ...p, active: false }
      );
      renderPhaseStrip();
    }

    // 3. Patch logical topology: add bidirectional phase links (as-is → logical/active)
    const logResp = await fetch(NC_BASE + '/api/topologies/' + newId);
    if (logResp.ok) {
      const logData = await logResp.json();
      const logGj   = logData.graph_json || { nodes: [], edges: [] };
      logGj._phases = [
        { topology_id: currentTopoId, label: 'as-is',  phase_order: 1, active: false },
        { topology_id: newId,         label: 'logical', phase_order: 2, active: true  },
      ];
      await fetch(NC_BASE + '/api/topologies/' + newId, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ graph_json: logGj }),
      });
    }

    // 4. Open in new tab
    window.open(NC_BASE + '/canvas/' + encodeURIComponent(newId), '_blank');

    // 5. Toast confirmation
    ncShowToast('Logical view created — opened in new tab', 'success');
    setStatus('Logical topology created → ' + newId);
  } catch (err) {
    ncShowToast('To Logical failed: ' + err.message, 'error');
    setStatus('To Logical failed: ' + err.message);
  } finally {
    if (btn) { btn.disabled = graph.getElements().length < 3; btn.innerHTML = origHTML; }
  }
}

// ─────────────────────────────────────────────────────────────────────────────

// Infer classification of an existing canvas node from its configData and label
function _nodeClassification(cell) {
  const cfg = cell.get ? cell.get('configData') || {} : (cell.config || {});
  // Check configData.classification, _classification, or label hints
  const explicit = cfg.classification || cfg._classification || '';
  if (explicit && CLASSIFICATION_RANK[explicit] !== undefined) return explicit.toUpperCase();
  const label = (cell.attr ? cell.attr('label/text') : cell.label) || '';
  if (/SIPR|SECRET/i.test(label)) return 'SECRET';
  if (/NIPR|CUI/i.test(label)) return 'CUI';
  if (/untrusted|internet|WAN|ISP|public/i.test(label)) return 'PUBLIC';
  // Zone hints from configData
  if (cfg.zone === 'untrusted') return 'PUBLIC';
  if (cfg.zone === 'dmz') return 'CUI';
  if (cfg.zone === 'trusted' || cfg.zone === 'management') return 'CUI';
  return null; // unknown — treat as same-level by default
}

function _classRank(cls) {
  return CLASSIFICATION_RANK[(cls || '').toUpperCase()] ?? -1;
}

// Would connecting these two classification levels violate security policy?
function _crossDomainViolation(snippetCls, existingCls) {
  const sRank = _classRank(snippetCls);
  const eRank = _classRank(existingCls);
  if (sRank < 0 || eRank < 0) return false; // unknown — allow (user decides)
  // SECRET/TS snippet cannot connect directly to PUBLIC/untrusted
  // (requires CDS or Type 1 encryptor in between — CNSS Policy 15, SC-7, CA-3)
  if (sRank >= 2 && eRank === 0) return true;
  // CUI snippet should not connect to PUBLIC without firewall
  // (NET-BND-001: firewall at boundary — SC-7)
  // We flag but don't hard-block — user may have a firewall in between
  return false;
}

const DIRECTION_ICONS = {
  upstream: '⬆',
  downstream: '⬇',
  monitoring: '👁',
  boundary: '🔒',
  warning: '⚠',
};

function buildIntegrationSuggestions(snippetNodes, idMap, snippet) {
  const snippetCls = (snippet.classification_level || 'CUI').toUpperCase();
  const snippetRank = _classRank(snippetCls);

  // ── Gather existing (non-snippet) nodes with classification ──
  const existingNodes = [];
  graph.getCells().forEach(cell => {
    if (!cell.isElement()) return;
    const cfg = cell.get('configData') || {};
    if (cfg._snippet_id) return; // skip other snippet-origin nodes
    const nodeType = cell.get('nodeType') || 'unknown';
    // Skip drawing shapes, text labels, headings, badges
    if (/^(draw-|text-|group-)/.test(nodeType)) return;
    existingNodes.push({
      id: cell.id,
      type: nodeType,
      label: cell.attr('label/text') || '',
      classification: _nodeClassification(cell),
      config: cfg,
      pos: cell.position(),
    });
  });

  if (!existingNodes.length) return { suggestions: [], warnings: [] };

  const suggestions = [];
  const warnings = [];
  const seen = new Set();

  // Helper: add a suggestion if classification rules allow it
  function suggest(sn, en, reason, protocol, direction, priority, controlRef) {
    const mappedId = idMap[sn.id];
    const key = `${mappedId}-${en.id}`;
    if (seen.has(key)) return;
    seen.add(key);

    const enCls = en.classification;
    const snCls = sn.config?.classification || snippetCls;

    // ── Hard block: cross-domain violation ──
    if (_crossDomainViolation(snCls, enCls)) {
      warnings.push({
        snippetNode: { id: mappedId, label: sn.label, type: sn.type },
        existingNode: { id: en.id, label: en.label, type: en.type },
        message: `BLOCKED: ${snCls} node "${sn.label}" cannot connect directly to ${enCls || 'untrusted'} node "${en.label}". ` +
                 `A Cross-Domain Solution (CDS) or NSA Type 1 encryptor is required between classification levels ` +
                 `(CNSS Policy 15, NIST SC-7, CA-3).`,
        controlRef: 'CA-3, SC-7, CNSS-15',
      });
      return; // do NOT add as a suggestion
    }

    // ── Warn: classification mismatch that needs encryption ──
    let warning = null;
    const snRank = _classRank(snCls);
    const enRank = _classRank(enCls);
    if (snRank >= 2 && enRank >= 0 && enRank < snRank) {
      // SECRET connecting to CUI — needs Type 1 or CDS
      warning = `Requires NSA Type 1 encryption or CDS between ${snCls} and ${enCls} domains (NET-ENC-002, CNSS Policy 15)`;
      protocol = 'Type 1 / HAIPE';
    } else if (snRank >= 1 && enRank >= 0 && enRank < snRank) {
      // CUI connecting to lower — needs FIPS 140 encryption
      warning = `FIPS 140-2 validated encryption required on this link (NET-ENC-004, SC-8)`;
    }

    suggestions.push({
      snippetNode: { id: mappedId, label: sn.label, type: sn.type },
      existingNode: { id: en.id, label: en.label, type: en.type },
      reason, protocol, direction, priority, controlRef,
      warning,
    });
  }

  // ── Apply rules ──
  for (const sn of snippetNodes) {
    const snCfg = sn.config || {};
    const snCls = snCfg.classification || snippetCls;
    const snRank = _classRank(snCls);
    const isOuterFW = /firewall|fw/i.test(sn.type) && /outer|perimeter|edge|untrusted/i.test(sn.label);
    const isInnerFW = /firewall|fw/i.test(sn.type) && /inner|internal|enclave|cui|trusted/i.test(sn.label);
    const isFW = TYPE_SETS.FIREWALL.has(sn.type);
    const isServer = TYPE_SETS.SERVER.has(sn.type);
    const isSIEM = TYPE_SETS.MONITORING.has(sn.type) || /siem|log|collector/i.test(sn.label);
    const isIDS = /ids|ips|sensor/i.test(sn.label);
    const isCDS = /cds|cross.domain|guard/i.test(sn.label) || sn.type === 'fips-140-l3';
    const isEncryptor = TYPE_SETS.TYPE1_ENCRYPTOR.has(sn.type) || TYPE_SETS.FIPS_ENCRYPTOR.has(sn.type);
    const isLB = /load.balancer|lb|proxy|waf/i.test(sn.type) || /load.balancer|proxy|waf/i.test(sn.label);
    const isWorkstation = /endpoint|workstation|pc/i.test(sn.type);
    const isTacticalRadio = /radio|pace|satcom/i.test(sn.label);

    for (const en of existingNodes) {
      const enRank = _classRank(en.classification);
      const sameOrHigherCls = enRank < 0 || enRank >= snRank; // unknown or same/higher = OK
      const sameCls = enRank < 0 || enRank === snRank;

      // ── RULE 1: Outer/perimeter firewall → same-classification router (NOT untrusted WAN) ──
      if (isOuterFW && TYPE_SETS.ROUTING.has(en.type) && sameOrHigherCls) {
        // Only connect to routers at the SAME or higher classification
        // Never connect SECRET FW directly to an untrusted/PUBLIC router
        suggest(sn, en,
          `Perimeter firewall connects to ${en.classification || 'internal'} routing layer (SC-7: boundary protection)`,
          snRank >= 2 ? 'Type 1 / HAIPE' : 'GbE', 'upstream', 1, 'SC-7, AC-4');
      }

      // ── RULE 2: Inner firewall → same-classification core/distribution switch ──
      if (isInnerFW && TYPE_SETS.SWITCHING.has(en.type) && sameOrHigherCls
          && /core|distrib|internal|enclave/i.test(en.label)) {
        suggest(sn, en,
          `Inner firewall guards the trusted enclave — connect to ${en.classification || ''} core/distribution layer (SC-7(3): boundary isolation)`,
          'GbE', 'downstream', 2, 'SC-7(3), AC-4');
      }

      // ── RULE 3: CDS connects ONLY across classification boundaries ──
      if (isCDS) {
        // CDS should connect to a LOWER classification node (that's its purpose)
        if (TYPE_SETS.FIREWALL.has(en.type) && enRank >= 0 && enRank < snRank) {
          suggest(sn, en,
            `CDS bridges ${snCls} → ${en.classification} boundary. NSA-evaluated guard required (CA-3, data-flow: ${snCfg.data_flow_direction || 'controlled'})`,
            'Type 1 / HAIPE', 'boundary', 1, 'CA-3, SC-7(21), CNSS-15');
        }
        // CDS should NOT connect to same-classification devices (pointless)
        continue;
      }

      // ── RULE 4: Type 1 / FIPS encryptor → WAN transport at boundary ──
      if (isEncryptor && TYPE_SETS.WAN_UNTRUSTED.has(en.type)) {
        // Encryptors DO connect to lower-classification transport — that's their job
        suggest(sn, en,
          `Encryptor secures ${snCls} data over ${en.classification || 'untrusted'} transport (NET-ENC-002: Type 1 for SECRET+)`,
          TYPE_SETS.TYPE1_ENCRYPTOR.has(sn.type) ? 'Type 1 / HAIPE' : 'FIPS 140',
          'boundary', 1, 'SC-8, SC-8(1), SC-13');
      }
      if (isEncryptor && TYPE_SETS.ROUTING.has(en.type) && sameOrHigherCls) {
        suggest(sn, en,
          `Encryptor inline with ${en.classification || 'internal'} router for encrypted WAN handoff`,
          'GbE', 'upstream', 2, 'SC-8, SC-13');
      }

      // ── RULE 5: SIEM/log collector — same classification only (AU-2, AU-9) ──
      if (isSIEM && sameCls && (TYPE_SETS.FIREWALL.has(en.type) || TYPE_SETS.ROUTING.has(en.type) || TYPE_SETS.SERVER.has(en.type))) {
        suggest(sn, en,
          `SIEM collects audit logs from ${en.label} via encrypted syslog (AU-2: audit events, AU-9: audit protection). Same ${snCls} domain only.`,
          'syslog-TLS', 'monitoring', 4, 'AU-2, AU-9, AU-12');
      }

      // ── RULE 6: IDS/IPS sensor — same-classification SPAN/mirror ──
      if (isIDS && sameCls && (TYPE_SETS.SWITCHING.has(en.type) || TYPE_SETS.FIREWALL.has(en.type))) {
        suggest(sn, en,
          `IDS/IPS receives mirrored traffic from ${en.label} via SPAN port for threat detection (SI-4: system monitoring). Must remain within ${snCls} domain.`,
          'SPAN', 'monitoring', 4, 'SI-4, SI-3');
      }

      // ── RULE 7: Servers → same-classification switches only ──
      if (isServer && TYPE_SETS.SWITCHING.has(en.type) && sameOrHigherCls) {
        suggest(sn, en,
          `Server connects to ${en.classification || ''} access/distribution switch (SC-7(3): micro-segmentation)`,
          'GbE', 'downstream', 5, 'SC-7(3), AC-4');
      }

      // ── RULE 8: Load balancer / reverse proxy → same-classification servers and firewalls ──
      if (isLB && (TYPE_SETS.SERVER.has(en.type) || TYPE_SETS.FIREWALL.has(en.type)) && sameOrHigherCls) {
        suggest(sn, en,
          `${sn.label} distributes traffic within ${snCls} domain — connect to ${en.label}`,
          'HTTPS', 'downstream', 5, 'SC-7, AC-4');
      }

      // ── RULE 9: Workstations → same-classification access switches ──
      if (isWorkstation && TYPE_SETS.SWITCHING.has(en.type) && sameOrHigherCls
          && /access|floor|user|building/i.test(en.label)) {
        suggest(sn, en,
          `${snCls} workstation connects to same-classification access switch (IA-2: CAC/MFA required)`,
          '1GbE', 'downstream', 6, 'IA-2, AC-17');
      }

      // ── RULE 10: Tactical radio/SATCOM → same-classification router ──
      if (isTacticalRadio && TYPE_SETS.ROUTING.has(en.type) && sameOrHigherCls) {
        suggest(sn, en,
          `Tactical comms connect to ${snCls} router for PACE transport (CP-8: telecom services)`,
          'Type 1 / HAIPE', 'upstream', 3, 'CP-8, SC-8');
      }
    }
  }

  // Sort by priority
  suggestions.sort((a, b) => a.priority - b.priority);
  return { suggestions: suggestions.slice(0, 12), warnings };
}

function showIntegrationGuide(result, snippet) {
  const { suggestions, warnings } = result;
  const overlay = document.getElementById('integration-guide-overlay');
  const content = document.getElementById('integration-guide-content');

  // Build header with classification badge
  const clColor = SNIPPET_CLASSIFICATION_COLORS[snippet.classification_level] || '#7a8cb0';
  let html = `
    <div style="background:rgba(92,66,217,0.12);border:1px solid rgba(92,66,217,0.3);border-radius:6px;padding:10px 12px;">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
        <span style="font-size:13px;font-weight:600;color:#a29bfe;">&#x229e; ${snippet.name}</span>
        <span style="font-size:10px;font-weight:700;color:${clColor};border:1px solid ${clColor};border-radius:3px;padding:1px 5px;">${snippet.classification_level}</span>
      </div>
      <div style="font-size:11px;color:var(--text-dim,#7a8cb0);line-height:1.5;">${snippet.description || ''}</div>
      <div style="font-size:10px;color:var(--text-dim,#7a8cb0);margin-top:6px;">
        Impact Level: ${snippet.impact_level} &bull;
        Suggestions are filtered by classification compatibility
      </div>
    </div>`;

  // ── Security warnings (cross-domain violations) ──
  if (warnings && warnings.length) {
    html += `
      <div style="background:rgba(231,76,60,0.1);border:1px solid rgba(231,76,60,0.4);border-radius:6px;padding:10px 12px;">
        <div style="font-size:12px;font-weight:600;color:#e74c3c;margin-bottom:6px;">
          &#x26A0; Security Boundary Violations Blocked
        </div>`;
    for (const w of warnings) {
      const snStyle = getStyle(w.snippetNode.type);
      const exStyle = getStyle(w.existingNode.type);
      html += `
        <div style="margin-bottom:8px;padding:6px 8px;background:rgba(231,76,60,0.06);border-radius:4px;">
          <div style="display:flex;align-items:center;gap:4px;margin-bottom:4px;">
            <span style="font-size:11px;font-weight:600;color:${snStyle.stroke};">${w.snippetNode.label}</span>
            <span style="font-size:10px;color:#e74c3c;">&#x26D4;</span>
            <span style="font-size:11px;font-weight:600;color:${exStyle.stroke};">${w.existingNode.label}</span>
          </div>
          <div style="font-size:10px;color:#e07070;line-height:1.4;">${w.message}</div>
          <div style="font-size:9px;color:#7a8cb0;margin-top:3px;">Controls: ${w.controlRef}</div>
        </div>`;
    }
    html += `</div>`;
  }

  // ── Suggestions ──
  if (!suggestions.length && (!warnings || !warnings.length)) {
    html += `
      <div style="color:var(--text-dim,#7a8cb0);font-size:12px;text-align:center;padding:20px;">
        No matching integration points found on the current canvas.<br>
        <span style="font-size:11px;">Add routers, switches, or firewalls to the canvas first, then insert a snippet to see connection suggestions.</span>
      </div>`;
  } else if (suggestions.length) {
    html += `
      <div style="font-size:11px;color:var(--text-dim,#7a8cb0);padding:0 2px;">
        <strong style="color:var(--text,#eaeaea);">${suggestions.length} compliant connection${suggestions.length > 1 ? 's' : ''}</strong>
        — classification-validated per NIST 800-53 / CNSS Policy 15.
      </div>`;

    for (const s of suggestions) {
      const icon = DIRECTION_ICONS[s.direction] || '&#x2194;';
      const snStyle = getStyle(s.snippetNode.type);
      const exStyle = getStyle(s.existingNode.type);
      // Warning badge for connections that need encryption
      const warnHtml = s.warning
        ? `<div style="font-size:10px;color:#f39c12;margin:4px 0 6px;padding:4px 6px;background:rgba(243,156,18,0.08);border-radius:3px;line-height:1.3;">&#x26A0; ${s.warning}</div>`
        : '';
      html += `
        <div style="background:var(--card-bg,#16213e);border:1px solid var(--border,#2a3a5e);border-radius:6px;padding:10px 12px;">
          <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;">
            <span style="font-size:16px;">${icon}</span>
            <span style="font-size:12px;font-weight:600;color:${snStyle.stroke};">${s.snippetNode.label}</span>
            <span style="font-size:10px;color:var(--text-dim);">&#x2192;</span>
            <span style="font-size:12px;font-weight:600;color:${exStyle.stroke};">${s.existingNode.label}</span>
          </div>
          <div style="font-size:11px;color:var(--text-dim,#7a8cb0);line-height:1.4;">${s.reason}</div>
          ${warnHtml}
          <div style="display:flex;align-items:center;gap:6px;margin-top:6px;">
            <button class="btn btn-sm btn-primary" style="flex:1;background:#5c42d9;border-color:#5c42d9;font-size:11px;"
              onclick="applyIntegrationLink('${s.snippetNode.id}','${s.existingNode.id}','${s.protocol}',this)">
              &#x1f517; Connect (${s.protocol})</button>
            <button class="btn btn-sm" style="background:transparent;border:1px solid var(--border,#2a3a5e);color:var(--text-dim,#7a8cb0);font-size:11px;"
              onclick="this.closest('div[style*=card-bg]').remove()">Skip</button>
            <span style="font-size:9px;color:#636e72;" title="${s.controlRef || ''}">${s.controlRef || ''}</span>
          </div>
        </div>`;
    }
  }

  content.innerHTML = html;
  overlay.classList.remove('hidden');
}

function applyIntegrationLink(srcId, tgtId, protocol, btn) {
  pushUndo();
  createLink(srcId, tgtId, protocol, protocol);
  markDirty();
  updateStatusBar();
  // Visual feedback
  const card = btn.closest('div[style*="card-bg"]');
  if (card) {
    card.style.borderColor = '#27ae60';
    card.style.opacity = '0.6';
    card.innerHTML = '<div style="color:#27ae60;font-size:11px;padding:6px;text-align:center;">✓ Connected</div>';
  }
}

function closeIntegrationGuide() {
  document.getElementById('integration-guide-overlay').classList.add('hidden');
}

async function insertSnippetOntoCanvas(snippetId, btn) {
  const origText = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = 'Inserting…'; }

  try {
    const r = await fetch(NC_BASE + `/api/snippets/${snippetId}`);
    if (!r.ok) throw new Error('Snippet not found');
    const snippet = await r.json();
    const gj = snippet.graph_json || { nodes: [], edges: [] };
    if (!gj.nodes.length) throw new Error('Snippet has no nodes');

    pushUndo();

    // ── 1. Collect bounding boxes of all existing nodes ──
    const PAD = 40; // padding between snippet group and existing nodes
    const existingBoxes = [];
    graph.getCells().forEach(cell => {
      if (!cell.isElement()) return;
      const pos = cell.position();
      const sz = cell.size();
      existingBoxes.push({
        x: pos.x - PAD, y: pos.y - PAD,
        w: sz.width + PAD * 2, h: sz.height + PAD * 2,
      });
    });

    // ── 2. Compute snippet bounding box (relative coords) ──
    const NODE_W = 110, NODE_H = 70;
    const snipXs = gj.nodes.map(n => n.x || 0);
    const snipYs = gj.nodes.map(n => n.y || 0);
    const snipMinX = Math.min(...snipXs);
    const snipMinY = Math.min(...snipYs);
    const snipW = Math.max(...snipXs) - snipMinX + NODE_W;
    const snipH = Math.max(...snipYs) - snipMinY + NODE_H;
    const snipCx = snipMinX + snipW / 2;
    const snipCy = snipMinY + snipH / 2;

    // ── 3. Compute ideal placement at visible viewport center ──
    const tx = paper.translate().tx || 0;
    const ty = paper.translate().ty || 0;
    const sx = paper.scale().sx || 1;
    const vpEl = document.querySelector('.canvas-body') || document.getElementById('canvas-container') || paper.el;
    const cw = vpEl ? vpEl.clientWidth : 800;
    const ch = vpEl ? vpEl.clientHeight : 600;
    const scrollX = vpEl ? vpEl.scrollLeft : 0;
    const scrollY = vpEl ? vpEl.scrollTop : 0;
    const vpCx = Math.round((scrollX + cw / 2 - tx) / sx);
    const vpCy = Math.round((scrollY + ch / 2 - ty) / sx);

    // ── 4. Find a clear position (no overlap with existing nodes) ──
    function snippetBoxAt(ox, oy) {
      // Bounding box of the entire snippet group when placed with this offset
      return {
        x: snipMinX + ox, y: snipMinY + oy,
        w: snipW, h: snipH,
      };
    }
    function overlaps(box) {
      return existingBoxes.some(eb =>
        box.x < eb.x + eb.w && box.x + box.w > eb.x &&
        box.y < eb.y + eb.h && box.y + box.h > eb.y
      );
    }

    // Start with viewport-center offset
    let offsetX = vpCx - snipCx;
    let offsetY = vpCy - snipCy;

    if (existingBoxes.length && overlaps(snippetBoxAt(offsetX, offsetY))) {
      // Spiral outward from viewport center to find a clear spot
      let found = false;
      const step = Math.max(snipW, snipH) + PAD;
      for (let ring = 1; ring <= 10 && !found; ring++) {
        const candidates = [
          { dx: ring * step, dy: 0 },          // right
          { dx: 0, dy: ring * step },           // below
          { dx: -ring * step, dy: 0 },          // left
          { dx: 0, dy: -ring * step },          // above
          { dx: ring * step, dy: ring * step },  // bottom-right
          { dx: -ring * step, dy: ring * step }, // bottom-left
          { dx: ring * step, dy: -ring * step }, // top-right
          { dx: -ring * step, dy: -ring * step },// top-left
        ];
        for (const c of candidates) {
          const tryOx = vpCx - snipCx + c.dx;
          const tryOy = vpCy - snipCy + c.dy;
          if (!overlaps(snippetBoxAt(tryOx, tryOy))) {
            offsetX = tryOx;
            offsetY = tryOy;
            found = true;
            break;
          }
        }
      }
      // If no clear spot found after 10 rings, place to the right of all content
      if (!found) {
        const maxX = existingBoxes.reduce((m, b) => Math.max(m, b.x + b.w), 0);
        offsetX = maxX + PAD - snipMinX;
        offsetY = vpCy - snipCy;
      }
    }

    // ── 5. Re-map node IDs to avoid collisions with existing nodes ──
    const suffix = '-' + Math.random().toString(36).slice(2, 7);
    const idMap = {};
    gj.nodes.forEach(n => { idMap[n.id] = n.id + suffix; });

    // ── 6. Insert nodes at computed position ──
    gj.nodes.forEach(n => {
      const cfg = n.config || {};
      cfg._snippet_id = snippetId;
      cfg._snippet_name = snippet.name;
      cfg._classification = snippet.classification_level;
      cfg._impact_level = snippet.impact_level;
      createNode(n.type, (n.x || 0) + offsetX, (n.y || 0) + offsetY, n.label, idMap[n.id], cfg);
    });

    // ── 7. Insert edges with remapped IDs ──
    gj.edges.forEach(e => {
      const src = idMap[e.source] || e.source;
      const dst = idMap[e.target] || e.target;
      if (src && dst) {
        createLink(src, dst, e.label || '', e.protocol || '');
      }
    });

    // ── 8. Scroll viewport to show the inserted snippet ──
    const finalBox = snippetBoxAt(offsetX, offsetY);
    const targetScrollX = (finalBox.x + finalBox.w / 2) * sx + tx - cw / 2;
    const targetScrollY = (finalBox.y + finalBox.h / 2) * sx + ty - ch / 2;
    if (vpEl) {
      vpEl.scrollTo({ left: Math.max(0, targetScrollX), top: Math.max(0, targetScrollY), behavior: 'smooth' });
    }

    markDirty();
    updateStatusBar();
    setStatus(`Snippet inserted: ${snippet.name} (${gj.nodes.length} nodes, ${gj.edges.length} links)`);
    closeSnippetsPanel();

    // ── 9. Show Integration Guide with connection suggestions ──
    const suggestions = buildIntegrationSuggestions(gj.nodes, idMap, snippet);
    showIntegrationGuide(suggestions, snippet);
  } catch (err) {
    console.error('[Snippet Insert]', err);
    setStatus('Insert failed: ' + err.message);
    // Show error visually inside the snippets panel (status bar may be hidden behind overlay)
    const errDiv = document.createElement('div');
    errDiv.style.cssText = 'background:#3b1010;border:1px solid #e74c3c;border-radius:4px;padding:8px 12px;margin:8px 4px 0;font-size:11px;color:#e74c3c;';
    errDiv.textContent = 'Insert failed: ' + err.message;
    const list = document.getElementById('snippets-list');
    if (list) list.prepend(errDiv);
    if (btn) { btn.disabled = false; btn.textContent = origText; }
  }
}

/* ── New canvas ───────────────────────────────────────────────────────────────── */
function newCanvas() {
  if (isDirty && !confirm('You have unsaved changes. Start a new canvas?')) return;
  pushUndo();
  graph.clear();
  currentTopoId = 'new';
  document.getElementById('topo-name-display').textContent = 'Untitled Topology';
  window.history.replaceState({}, '', '/canvas/new');
  _phases = [];
  renderPhaseStrip();
  deselectAll();
  updateStatusBar();
  isDirty = false;
  setStatus('New canvas — drag objects from the palette');
}

/* ── Status helpers ───────────────────────────────────────────────────────────── */
function markDirty() {
  isDirty = true;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveTopology, 3000); // autosave to server after 3s idle
  _localBackup(); // immediate localStorage backup (crash recovery)
  updateStatusBar();
}

function setStatus(msg) {
  document.getElementById('tb-status').textContent = msg;
}

function updateStatusBar() {
  if (!graph) return;
  const elements = graph.getElements().length;
  const links = graph.getLinks().length;
  document.getElementById('sb-objects').textContent = `Objects: ${elements}`;
  document.getElementById('sb-links').textContent = `Links: ${links}`;
}

/* ── Local Backup (localStorage) — crash recovery ────────────────────────────── */
const _LS_KEY_PREFIX = 'nc-backup-';
const _LS_MAX_BACKUPS = 10; // keep last 10 snapshots per topology

function _localBackup() {
  if (!graph) return;
  try {
    const gj = graphToJSON();
    const topoId = currentTopoId || 'new';
    const key = _LS_KEY_PREFIX + topoId;
    const entry = {
      timestamp: new Date().toISOString(),
      topoId: topoId,
      name: document.getElementById('topo-name-display')?.textContent || 'Untitled',
      nodeCount: gj.nodes.length,
      edgeCount: gj.edges.length,
      graph_json: gj,
    };
    // Save current snapshot
    localStorage.setItem(key, JSON.stringify(entry));
    // Also maintain a rolling history
    const histKey = key + '-history';
    let history = [];
    try { history = JSON.parse(localStorage.getItem(histKey) || '[]'); } catch(e) {}
    history.unshift({ timestamp: entry.timestamp, nodeCount: entry.nodeCount, edgeCount: entry.edgeCount });
    if (history.length > _LS_MAX_BACKUPS) history = history.slice(0, _LS_MAX_BACKUPS);
    localStorage.setItem(histKey, JSON.stringify(history));
    // Update status bar backup indicator
    const sbSaved = document.getElementById('sb-saved');
    if (sbSaved) {
      const t = new Date().toLocaleTimeString();
      sbSaved.textContent = `Local backup: ${t}`;
    }
  } catch (e) {
    // localStorage full or unavailable — non-fatal
    console.warn('Local backup failed:', e);
  }
}

function _checkLocalRecovery() {
  // On page load, check if there's a more recent local backup than what's on the server
  const topoId = currentTopoId || 'new';
  const key = _LS_KEY_PREFIX + topoId;
  try {
    const saved = localStorage.getItem(key);
    if (!saved) return;
    const entry = JSON.parse(saved);
    if (!entry.graph_json || !entry.graph_json.nodes) return;
    // Only offer recovery if the backup has content and is recent (< 24h)
    const backupAge = Date.now() - new Date(entry.timestamp).getTime();
    if (backupAge > 24 * 60 * 60 * 1000) return; // older than 24h, skip
    if (entry.nodeCount === 0) return; // empty backup, skip
    // Show recovery banner
    const banner = document.createElement('div');
    banner.id = 'recovery-banner';
    banner.style.cssText = 'position:fixed;bottom:60px;left:50%;transform:translateX(-50%);z-index:300;background:#2b1a0f;border:1px solid #f39c12;border-radius:8px;padding:12px 20px;color:#eaeaea;font-size:13px;display:flex;align-items:center;gap:12px;box-shadow:0 4px 20px rgba(0,0,0,0.4);';
    banner.innerHTML = `
      <span style="color:#f39c12;font-size:16px;">&#9888;</span>
      <span>Local backup found (${entry.nodeCount} nodes, ${new Date(entry.timestamp).toLocaleString()})</span>
      <button onclick="restoreFromLocal()" style="padding:4px 12px;background:#f39c12;color:#000;border:none;border-radius:4px;font-weight:600;cursor:pointer;">Restore</button>
      <button onclick="dismissRecovery()" style="padding:4px 12px;background:transparent;color:#7a8cb0;border:1px solid #7a8cb0;border-radius:4px;cursor:pointer;">Dismiss</button>
    `;
    document.body.appendChild(banner);
  } catch(e) {}
}

function restoreFromLocal() {
  const topoId = currentTopoId || 'new';
  const key = _LS_KEY_PREFIX + topoId;
  try {
    const entry = JSON.parse(localStorage.getItem(key));
    if (entry && entry.graph_json) {
      pushUndo();
      loadGraphJSON(entry.graph_json);
      updateStatusBar();
      markDirty();
      setStatus('Restored from local backup (' + entry.nodeCount + ' nodes)');
    }
  } catch(e) {
    setStatus('Recovery failed: ' + e.message);
  }
  dismissRecovery();
}

function dismissRecovery() {
  const banner = document.getElementById('recovery-banner');
  if (banner) banner.remove();
}

/* ── Periodic Server Backup (every 30 minutes) ───────────────────────────────── */
let _backupTimer = null;

function _startPeriodicBackup() {
  // Trigger a server-side backup every 30 minutes
  _backupTimer = setInterval(async () => {
    if (!currentTopoId || currentTopoId === 'new') return;
    try {
      const r = await fetch(NC_BASE + '/api/backups', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({notes: 'auto-periodic', topology_id: currentTopoId}),
      });
      if (r.ok) {
        console.log('Periodic backup completed');
      }
    } catch(e) {
      console.warn('Periodic backup failed:', e);
    }
  }, 30 * 60 * 1000); // 30 minutes
}

function _stopPeriodicBackup() {
  if (_backupTimer) { clearInterval(_backupTimer); _backupTimer = null; }
}

/* ── Custom Device Creator ─────────────────────────────────────────────────── */
function openCustomDeviceDialog() {
  document.getElementById('custom-device-overlay').classList.remove('hidden');
  updateCustomDevicePreview();
  // Live preview
  ['cd-type-id', 'cd-label', 'cd-symbol', 'cd-fill', 'cd-stroke'].forEach(id => {
    document.getElementById(id).addEventListener('input', updateCustomDevicePreview);
  });
}

function closeCustomDeviceDialog() {
  document.getElementById('custom-device-overlay').classList.add('hidden');
}

function updateCustomDevicePreview() {
  const symbol = document.getElementById('cd-symbol').value || '?';
  const label = document.getElementById('cd-label').value || 'Custom';
  const fill = document.getElementById('cd-fill').value;
  const stroke = document.getElementById('cd-stroke').value;
  const preview = document.getElementById('cd-preview');
  preview.innerHTML = `<div style="width:110px;height:60px;background:${fill};border:2px solid ${stroke};border-radius:6px;display:flex;flex-direction:column;align-items:center;justify-content:center;">
    <span style="color:${stroke};font-size:11px;font-weight:bold;font-family:monospace;">${symbol}</span>
    <span style="color:#eaeaea;font-size:10px;margin-top:2px;">${label}</span>
  </div>`;
}

function createCustomDevice() {
  const typeId = document.getElementById('cd-type-id').value.trim().toLowerCase().replace(/\s+/g, '-');
  const label = document.getElementById('cd-label').value.trim();
  const symbol = document.getElementById('cd-symbol').value.trim();
  const fill = document.getElementById('cd-fill').value;
  const stroke = document.getElementById('cd-stroke').value;
  const bandwidth = document.getElementById('cd-bandwidth').value.trim();

  if (!typeId || !label || !symbol) {
    alert('Type ID, Display Name, and Symbol are required.');
    return;
  }
  if (NODE_STYLES[typeId]) {
    alert('Type ID "' + typeId + '" already exists. Choose a different ID.');
    return;
  }

  // Register in NODE_STYLES
  NODE_STYLES[typeId] = { fill, stroke, label, symbol };

  // Add to palette
  const palette = document.getElementById('palette');
  let customSection = document.getElementById('palette-custom-section');
  if (!customSection) {
    customSection = document.createElement('div');
    customSection.id = 'palette-custom-section';
    customSection.className = 'palette-section';
    customSection.innerHTML = '<div class="palette-cat">Custom</div><div class="palette-items" id="palette-custom-items"></div>';
    palette.appendChild(customSection);
  }

  const items = document.getElementById('palette-custom-items');
  const div = document.createElement('div');
  div.className = 'palette-item';
  div.draggable = true;
  div.dataset.type = typeId;
  div.title = bandwidth ? `${label} — ${bandwidth}` : label;
  div.ondragstart = onDragStart;
  div.innerHTML = `<div class="pi-icon" style="background:${fill};border:1px solid ${stroke};color:${stroke};">${symbol}</div><span>${label}</span>`;
  items.appendChild(div);

  closeCustomDeviceDialog();
  setStatus(`Custom device "${label}" added to palette`);
}

/* ── Save design back to template ─────────────────────────────────────────────── */
function getSaveToTemplateId() {
  const params = new URLSearchParams(window.location.search);
  return params.get('save_to_template');
}

async function saveToTemplate() {
  const tplId = getSaveToTemplateId();
  if (!tplId) return;
  const gj = graphToJSON();
  setStatus('Saving to template...');
  try {
    const r = await fetch(NC_BASE + `/api/templates/${tplId}`, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({graph_json: gj}),
    });
    const data = await r.json();
    if (data.ok) {
      setStatus('Template design updated!');
    } else {
      setStatus('Error: ' + (data.error || 'unknown'));
    }
  } catch (err) {
    setStatus('Save to template failed: ' + err.message);
  }
}

/* ── Security Boundary Auto-Fencing ──────────────────────────────────────────── */
let fenceMode = false;
let fenceStart = null;
let fenceRect = null;
let boundaryZones = [];  // loaded from DB

const FENCE_CLASSIFICATIONS = ['CUI', 'SECRET', 'TOP SECRET', 'PUBLIC'];
const FENCE_CLS_COLORS = {
  'CUI': '#f39c12', 'SECRET': '#e94560',
  'TOP SECRET': '#9b59b6', 'PUBLIC': '#27ae60',
};

function toggleFenceMode() {
  fenceMode = !fenceMode;
  const btn = document.getElementById('tb-fence-btn');
  const indicator = document.getElementById('fence-indicator');
  if (btn) btn.classList.toggle('tb-btn-active', fenceMode);
  if (indicator) { indicator.textContent = fenceMode ? 'ON' : 'OFF'; indicator.style.opacity = fenceMode ? '1' : '0.6'; }
  if (fenceMode) {
    setStatus('Fence mode ON — click and drag a rectangle around devices to create security boundary');
    document.getElementById('canvas-container').style.cursor = 'crosshair';
  } else {
    setStatus('Fence mode OFF');
    document.getElementById('canvas-container').style.cursor = '';
    removeFenceRect();
  }
}

function removeFenceRect() {
  if (fenceRect) { fenceRect.remove(); fenceRect = null; }
  fenceStart = null;
}

function initFenceSelection() {
  const container = document.getElementById('canvas-container');
  if (!container) return;

  container.addEventListener('mousedown', (e) => {
    if (!fenceMode || e.button !== 0) return;
    const rect = container.getBoundingClientRect();
    const sx = paper.scale().sx;
    const tx = paper.translate().tx;
    const ty = paper.translate().ty;
    fenceStart = {
      clientX: e.clientX, clientY: e.clientY,
      x: (e.clientX - rect.left - tx) / sx,
      y: (e.clientY - rect.top - ty) / sx,
    };
    // Create visual selection rectangle
    removeFenceRect();
    fenceRect = document.createElement('div');
    fenceRect.className = 'fence-selection-rect';
    fenceRect.style.left = (e.clientX - rect.left) + 'px';
    fenceRect.style.top = (e.clientY - rect.top) + 'px';
    fenceRect.style.width = '0';
    fenceRect.style.height = '0';
    container.appendChild(fenceRect);
    e.preventDefault();
  });

  container.addEventListener('mousemove', (e) => {
    if (!fenceMode || !fenceStart || !fenceRect) return;
    const rect = container.getBoundingClientRect();
    const x1 = fenceStart.clientX - rect.left;
    const y1 = fenceStart.clientY - rect.top;
    const x2 = e.clientX - rect.left;
    const y2 = e.clientY - rect.top;
    fenceRect.style.left = Math.min(x1, x2) + 'px';
    fenceRect.style.top = Math.min(y1, y2) + 'px';
    fenceRect.style.width = Math.abs(x2 - x1) + 'px';
    fenceRect.style.height = Math.abs(y2 - y1) + 'px';
  });

  container.addEventListener('mouseup', (e) => {
    if (!fenceMode || !fenceStart) return;
    const rect = container.getBoundingClientRect();
    const sx = paper.scale().sx;
    const tx = paper.translate().tx;
    const ty = paper.translate().ty;
    const endX = (e.clientX - rect.left - tx) / sx;
    const endY = (e.clientY - rect.top - ty) / sx;

    const selX1 = Math.min(fenceStart.x, endX);
    const selY1 = Math.min(fenceStart.y, endY);
    const selX2 = Math.max(fenceStart.x, endX);
    const selY2 = Math.max(fenceStart.y, endY);

    // Find nodes inside selection rectangle
    const selectedNodes = [];
    graph.getElements().forEach(el => {
      const pos = el.position();
      const size = el.size();
      const cx = pos.x + size.width / 2;
      const cy = pos.y + size.height / 2;
      if (cx >= selX1 && cx <= selX2 && cy >= selY1 && cy <= selY2) {
        selectedNodes.push({
          id: el.id,
          type: el.get('nodeType') || 'unknown',
          label: el.attr('label/text') || '',
        });
      }
    });

    removeFenceRect();

    if (selectedNodes.length === 0) {
      setStatus('No devices in selection area');
      return;
    }

    // Show boundary creation dialog
    showFenceDialog(selectedNodes);
  });
}

function showFenceDialog(selectedNodes) {
  const overlay = document.getElementById('fence-overlay');
  if (!overlay) return;
  overlay.classList.remove('hidden');

  // Populate node list
  const nodeList = document.getElementById('fence-node-list');
  nodeList.innerHTML = selectedNodes.map(n =>
    `<div class="fence-node-item"><span class="fence-node-type">${n.type}</span> ${n.label}</div>`
  ).join('');
  document.getElementById('fence-node-count').textContent = selectedNodes.length;

  // Store selected node IDs for submission
  overlay.dataset.nodeIds = JSON.stringify(selectedNodes.map(n => n.id));
}

function closeFenceDialog() {
  document.getElementById('fence-overlay').classList.add('hidden');
}

async function createBoundaryFromFence() {
  const overlay = document.getElementById('fence-overlay');
  const nodeIds = JSON.parse(overlay.dataset.nodeIds || '[]');
  const classification = document.getElementById('fence-classification').value;
  const label = document.getElementById('fence-label').value.trim();
  const notes = document.getElementById('fence-notes').value.trim();

  if (!currentTopoId || currentTopoId === 'new') {
    await saveTopology();
  }

  setStatus('Creating boundary...');
  try {
    const r = await fetch(NC_BASE + `/api/boundaries/${currentTopoId}/auto-fence`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        node_ids: nodeIds,
        classification,
        label: label || undefined,
        notes,
        snap_grid: 10,
        padding: 40,
      }),
    });
    const data = await r.json();
    if (r.ok) {
      closeFenceDialog();
      renderBoundaryZone(data);
      boundaryZones.push(data);
      updateBoundaryPanel();
      setStatus(`Boundary created: ${data.label} (${data.stig_tags.length} STIG tags)`);
    } else {
      setStatus('Error: ' + (data.error || 'unknown'));
    }
  } catch (err) {
    setStatus('Boundary creation failed: ' + err.message);
  }
}

function renderBoundaryZone(b) {
  const container = document.getElementById('canvas-container');
  if (!container) return;

  // Remove existing boundary visual if present
  const existing = document.getElementById('boundary-' + b.id);
  if (existing) existing.remove();

  const sx = paper.scale().sx;
  const tx = paper.translate().tx;
  const ty = paper.translate().ty;

  const zone = document.createElement('div');
  zone.id = 'boundary-' + b.id;
  zone.className = 'boundary-zone';
  zone.style.left = (b.pos_x * sx + tx) + 'px';
  zone.style.top = (b.pos_y * sx + ty) + 'px';
  zone.style.width = (b.width * sx) + 'px';
  zone.style.height = (b.height * sx) + 'px';
  // Separate fill color (semi-transparent RGBA) from solid color used for border/badge.
  // This lets the zone background be transparent enough to see nodes inside,
  // while the dashed border and label badge use a solid color for legibility.
  const fillColor = b.color || 'rgba(92,92,138,0.15)';
  // Strip alpha from rgba(...) to get a solid rgb(...) for border and badge.
  let solidColor = fillColor.replace(
    /rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)[^)]*\)/i, 'rgb($1,$2,$3)'
  );
  // Allow an explicit border_color override stored in the boundary notes JSON.
  try {
    const meta = JSON.parse(b.notes || '{}');
    if (meta.border_color) solidColor = meta.border_color;
  } catch (_) {}

  zone.style.borderColor = solidColor;     // solid dashed border — clearly visible
  zone.style.backgroundColor = fillColor;  // semi-transparent fill — nodes show through
  zone.style.setProperty('--fence-opacity', b.fill_opacity || 0.08);

  // Classification label badge — solid background ensures white text contrast
  const badge = document.createElement('div');
  badge.className = 'boundary-label';
  badge.style.backgroundColor = solidColor;
  badge.textContent = b.label || b.classification;
  zone.appendChild(badge);

  // STIG tag count badge
  if (b.stig_tags && b.stig_tags.length > 0) {
    const stigBadge = document.createElement('div');
    stigBadge.className = 'boundary-stig-badge';
    stigBadge.textContent = `${b.stig_tags.length} STIG`;
    stigBadge.title = b.stig_tags.join('\n');
    zone.appendChild(stigBadge);
  }

  zone.dataset.boundaryId = b.id;
  zone.title = `${b.label} — ${b.classification}\n${(b.stig_tags || []).join('\n')}`;
  container.appendChild(zone);
}

function renderAllBoundaries() {
  // Remove existing boundary visuals
  document.querySelectorAll('.boundary-zone').forEach(el => el.remove());
  boundaryZones.forEach(b => renderBoundaryZone(b));
}

async function loadBoundaries() {
  if (!currentTopoId || currentTopoId === 'new') return;
  try {
    const r = await fetch(NC_BASE + `/api/boundaries/${currentTopoId}`);
    if (r.ok) {
      boundaryZones = await r.json();
      renderAllBoundaries();
      updateBoundaryPanel();
    }
  } catch (_e) { /* silent */ }
}

async function deleteBoundary(bid) {
  if (!confirm('Delete this security boundary?')) return;
  try {
    await fetch(NC_BASE + `/api/boundaries/${currentTopoId}/${bid}`, { method: 'DELETE' });
    boundaryZones = boundaryZones.filter(b => b.id !== bid);
    const el = document.getElementById('boundary-' + bid);
    if (el) el.remove();
    updateBoundaryPanel();
    setStatus('Boundary deleted');
  } catch (err) {
    setStatus('Delete failed: ' + err.message);
  }
}

function updateBoundaryPanel() {
  const panel = document.getElementById('boundary-list');
  if (!panel) return;
  if (boundaryZones.length === 0) {
    panel.innerHTML = '<div class="boundary-empty">No boundaries defined. Use fence tool to create.</div>';
    return;
  }
  panel.innerHTML = boundaryZones.map(b => `
    <div class="boundary-list-item" onmouseover="highlightBoundary('${b.id}')" onmouseout="unhighlightBoundary('${b.id}')">
      <div class="boundary-list-color" style="background:${b.color}"></div>
      <div class="boundary-list-info">
        <div class="boundary-list-label">${b.label}</div>
        <div class="boundary-list-meta">${b.classification} · ${(b.node_ids || []).length} devices · ${(b.stig_tags || []).length} STIG tags</div>
      </div>
      <button class="boundary-list-del" onclick="deleteBoundary('${b.id}')" title="Delete boundary">×</button>
    </div>
  `).join('');
}

function highlightBoundary(bid) {
  const el = document.getElementById('boundary-' + bid);
  if (el) el.classList.add('boundary-highlight');
}

function unhighlightBoundary(bid) {
  const el = document.getElementById('boundary-' + bid);
  if (el) el.classList.remove('boundary-highlight');
}

// Reposition boundary visuals on pan/zoom
function repositionBoundaries() {
  if (!paper) return;
  const sx = paper.scale().sx;
  const tx = paper.translate().tx;
  const ty = paper.translate().ty;
  boundaryZones.forEach(b => {
    const el = document.getElementById('boundary-' + b.id);
    if (!el) return;
    el.style.left = (b.pos_x * sx + tx) + 'px';
    el.style.top = (b.pos_y * sx + ty) + 'px';
    el.style.width = (b.width * sx) + 'px';
    el.style.height = (b.height * sx) + 'px';
  });
}

/* ── Init ─────────────────────────────────────────────────────────────────────── */
/* ── Cable Run Annotation Dialog ──────────────────────────────────────────── */
function openCableAnnotationDialog(link) {
  // Remove existing dialog if any
  closeCableAnnotationDialog();

  const cable = link.get('cableData') || {};
  const src = graph.getCell(link.get('source')?.id);
  const tgt = graph.getCell(link.get('target')?.id);
  const srcLabel = src?.attr?.('label/text') || '?';
  const tgtLabel = tgt?.attr?.('label/text') || '?';

  const overlay = document.createElement('div');
  overlay.id = 'cable-annotation-overlay';
  overlay.className = 'cable-annotation-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) closeCableAnnotationDialog(); };

  overlay.innerHTML = `
    <div class="cable-annotation-dialog">
      <div class="cable-dialog-header">
        <span>Cable Run: ${srcLabel} → ${tgtLabel}</span>
        <button class="cable-dialog-close" onclick="closeCableAnnotationDialog()">&times;</button>
      </div>
      <div class="cable-dialog-body">
        <div class="form-group">
          <label>Cable Type</label>
          <select id="cable-type" class="form-control">
            <option value="">— Select —</option>
            <option value="SMF" ${cable.cable_type === 'SMF' ? 'selected' : ''}>SMF (Single-Mode Fiber)</option>
            <option value="MMF-OM3" ${cable.cable_type === 'MMF-OM3' ? 'selected' : ''}>MMF OM3 (Multi-Mode Fiber)</option>
            <option value="MMF-OM4" ${cable.cable_type === 'MMF-OM4' ? 'selected' : ''}>MMF OM4 (Multi-Mode Fiber)</option>
            <option value="MMF-OM5" ${cable.cable_type === 'MMF-OM5' ? 'selected' : ''}>MMF OM5 (Multi-Mode Fiber)</option>
            <option value="Cat5e" ${cable.cable_type === 'Cat5e' ? 'selected' : ''}>Cat5e (Copper)</option>
            <option value="Cat6" ${cable.cable_type === 'Cat6' ? 'selected' : ''}>Cat6 (Copper)</option>
            <option value="Cat6A" ${cable.cable_type === 'Cat6A' ? 'selected' : ''}>Cat6A (Copper)</option>
            <option value="Cat8" ${cable.cable_type === 'Cat8' ? 'selected' : ''}>Cat8 (Copper)</option>
            <option value="Coax-RG6" ${cable.cable_type === 'Coax-RG6' ? 'selected' : ''}>Coax RG-6</option>
            <option value="Coax-RG11" ${cable.cable_type === 'Coax-RG11' ? 'selected' : ''}>Coax RG-11</option>
            <option value="DAC" ${cable.cable_type === 'DAC' ? 'selected' : ''}>DAC (Direct Attach Copper)</option>
            <option value="AOC" ${cable.cable_type === 'AOC' ? 'selected' : ''}>AOC (Active Optical Cable)</option>
          </select>
        </div>
        <div class="form-group">
          <label>Distance (meters)</label>
          <input type="number" id="cable-distance" class="form-control" placeholder="e.g. 150" value="${cable.distance_m || ''}" min="0" step="0.1"/>
        </div>
        <div class="form-group">
          <label>Conduit ID</label>
          <input type="text" id="cable-conduit" class="form-control" placeholder="e.g. CDT-A-001" value="${cable.conduit_id || ''}"/>
        </div>
        <div class="form-group">
          <label>Fiber Strand Assignment</label>
          <input type="text" id="cable-strands" class="form-control" placeholder="e.g. 1-2, 3-4" value="${cable.fiber_strands || ''}"/>
        </div>
        <div class="form-group">
          <label>Pull Tension (lbs)</label>
          <input type="number" id="cable-tension" class="form-control" placeholder="e.g. 25" value="${cable.pull_tension_lbs || ''}" min="0" step="0.1"/>
        </div>
        <div class="form-group">
          <label>Notes</label>
          <textarea id="cable-notes" class="form-control" rows="2" placeholder="e.g. routed through ceiling plenum">${cable.notes || ''}</textarea>
        </div>
      </div>
      <div class="cable-dialog-footer">
        <button class="tb-btn" onclick="closeCableAnnotationDialog()">Cancel</button>
        <button class="tb-btn cable-btn-clear" onclick="clearCableAnnotation()">Clear</button>
        <button class="tb-btn cable-btn-save" onclick="saveCableAnnotation()">Save</button>
      </div>
    </div>
  `;

  document.body.appendChild(overlay);
  // Store reference to the link being edited
  overlay._editingLink = link;
}

function closeCableAnnotationDialog() {
  const overlay = document.getElementById('cable-annotation-overlay');
  if (overlay) overlay.remove();
}

function saveCableAnnotation() {
  const overlay = document.getElementById('cable-annotation-overlay');
  if (!overlay || !overlay._editingLink) return;

  const link = overlay._editingLink;
  pushUndo();

  const cableData = {};
  const cableType = document.getElementById('cable-type').value;
  const distance = document.getElementById('cable-distance').value;
  const conduit = document.getElementById('cable-conduit').value;
  const strands = document.getElementById('cable-strands').value;
  const tension = document.getElementById('cable-tension').value;
  const notes = document.getElementById('cable-notes').value;

  if (cableType) cableData.cable_type = cableType;
  if (distance) cableData.distance_m = parseFloat(distance);
  if (conduit) cableData.conduit_id = conduit;
  if (strands) cableData.fiber_strands = strands;
  if (tension) cableData.pull_tension_lbs = parseFloat(tension);
  if (notes) cableData.notes = notes;

  link.set('cableData', Object.keys(cableData).length ? cableData : undefined);

  // Update link label with cable type abbreviation if set
  if (cableType) {
    const existingLabels = link.labels();
    const shortLabel = cableType + (distance ? ` ${distance}m` : '');
    if (existingLabels.length > 0) {
      link.label(0, {
        attrs: { text: { text: shortLabel, fill: '#a29bfe', fontSize: 9, fontFamily: 'Cascadia Code, Consolas, monospace' } },
        position: 0.5
      });
    } else {
      link.appendLabel({
        attrs: { text: { text: shortLabel, fill: '#a29bfe', fontSize: 9, fontFamily: 'Cascadia Code, Consolas, monospace' } },
        position: 0.5
      });
    }
  }

  markDirty();
  closeCableAnnotationDialog();
}

function clearCableAnnotation() {
  const overlay = document.getElementById('cable-annotation-overlay');
  if (!overlay || !overlay._editingLink) return;

  pushUndo();
  overlay._editingLink.set('cableData', undefined);
  markDirty();
  closeCableAnnotationDialog();
}

/* ── Cable Plant Report Export ───────────────────────────────────────────── */
async function exportCablePlantReport() {
  if (!currentTopoId || currentTopoId === 'new') {
    await saveTopology();
  }
  if (!currentTopoId || currentTopoId === 'new') {
    alert('Save the topology first before exporting.');
    return;
  }

  setStatus('Generating cable plant report...');
  try {
    const r = await fetch(NC_BASE + `/api/cable-plant-report/${currentTopoId}`);
    if (!r.ok) throw new Error('Export failed');
    const data = await r.json();
    if (data.csv) {
      const blob = new Blob([data.csv], { type: 'text/csv' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = data.filename || 'cable-plant-report.csv';
      a.click();
      setStatus('Cable plant report exported');
    }
  } catch (err) {
    setStatus('Cable plant export failed: ' + err.message);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  initCanvas();

  // If opened from template editor, show "Save to Template" button
  const tplId = getSaveToTemplateId();
  if (tplId) {
    const tbGroup = document.querySelector('.tb-group');
    if (tbGroup) {
      const btn = document.createElement('button');
      btn.className = 'tb-btn';
      btn.style.background = '#2b0f3a';
      btn.style.borderColor = '#9b59b6';
      btn.style.color = '#c39bd3';
      btn.textContent = '⬆ Save to Template';
      btn.title = 'Save current design back to template ' + tplId;
      btn.onclick = saveToTemplate;
      tbGroup.parentElement.insertBefore(btn, tbGroup.parentElement.querySelector('.tb-center'));
    }
    setStatus('Editing template — modify design, then click "Save to Template"');
  }

  // Initialize fence selection and load existing boundaries
  initFenceSelection();
  loadBoundaries();

  // ndc-ux-02: Start the live conflict scanner
  _initConflictScanner();

  // ndc-ai-04: Wire PatternAdvisor to all graph mutation events
  _initPatternAdvisor();

  // ndc-pl-03: Sync To Logical button disabled state with node count
  graph.on('add',    _updateToLogicalBtnState);
  graph.on('remove', _updateToLogicalBtnState);
  _updateToLogicalBtnState();
});

/* ═══════════════════════════════════════════════════════════════════════════
 * Heatmap Overlay — color nodes/links by metric (bandwidth, vuln, stig, age)
 * ═══════════════════════════════════════════════════════════════════════════ */

let _heatmapOverlayActive = null;   // null | 'bandwidth' | 'vuln' | 'stig' | 'age'
let _stigOverlayApplied   = false;  // true when STIG import colors are on canvas
const _originalNodeColors = {};     // nodeId -> {stencilBodyFill, bodyFill}
const _originalLinkColors = {};     // edgeId -> {stroke, strokeWidth}
const _heatmapValues      = {};     // cellId -> {metric, value, label}

/** Metric display config: title, low label, high label, value formatter. */
const _HEATMAP_META = {
  bandwidth: { title: 'Bandwidth Utilization', lo: '0%',    hi: '100%',  fmt: v => `${(v * 100).toFixed(0)}% utilization` },
  vuln:      { title: 'Vulnerability Severity', lo: 'None',  hi: 'CAT I', fmt: v => v >= 0.9 ? 'CAT I (Critical)' : v >= 0.5 ? 'CAT II (High)' : v >= 0.2 ? 'CAT III (Medium)' : 'No findings' },
  stig:      { title: 'STIG Compliance',        lo: '100%',  hi: '0%',    fmt: v => `${((1 - v) * 100).toFixed(0)}% compliant` },
  age:       { title: 'Equipment Age',          lo: 'New',   hi: '10+ yr', fmt: v => `${(v * 10).toFixed(1)} years` },
};

/** Interpolate a 0..1 value to a green→yellow→red gradient color. */
function _heatmapColor(val) {
  const v = Math.max(0, Math.min(1, val));
  if (v <= 0.5) {
    // green (#2ecc71) → yellow (#f39c12)
    const t = v * 2;
    const r = Math.round(46  + (243 - 46)  * t);
    const g = Math.round(204 + (156 - 204) * t);
    const b = Math.round(113 + (18  - 113) * t);
    return `rgb(${r},${g},${b})`;
  }
  // yellow (#f39c12) → red (#e74c3c)
  const t = (v - 0.5) * 2;
  const r = Math.round(243 + (231 - 243) * t);
  const g = Math.round(156 + (76  - 156) * t);
  const b = Math.round(18  + (60  - 18)  * t);
  return `rgb(${r},${g},${b})`;
}

/** Save current color and paint a node cell. */
function _applyNodeColor(cell, fillColor) {
  if (!cell || !cell.isElement()) return;
  const nid = cell.id;
  if (!_originalNodeColors[nid]) {
    _originalNodeColors[nid] = {
      stencilBodyFill: cell.attr('stencilBody/fill'),
      bodyFill:        cell.attr('body/fill'),
    };
  }
  // Stencil nodes use stencilBody/fill; generic rect nodes use body/fill
  const stencilD = cell.attr('stencilBody/d');
  if (stencilD && stencilD !== '') {
    cell.attr('stencilBody/fill', fillColor);
  } else {
    cell.attr('body/fill', fillColor);
  }
}

/** Restore a node cell to its saved original color. */
function _restoreNodeColor(cell) {
  if (!cell || !cell.isElement()) return;
  const orig = _originalNodeColors[cell.id];
  if (!orig) return;
  if (orig.stencilBodyFill !== undefined) cell.attr('stencilBody/fill', orig.stencilBodyFill);
  if (orig.bodyFill        !== undefined) cell.attr('body/fill',        orig.bodyFill);
  delete _originalNodeColors[cell.id];
}

/** Show the floating legend panel with metric-specific labels. */
function _showHeatmapLegend(metric) {
  const meta = _HEATMAP_META[metric] || { title: metric, lo: 'Low', hi: 'High' };
  const legend = document.getElementById('heatmap-legend');
  const title  = document.getElementById('heatmap-legend-title');
  const lo     = document.getElementById('heatmap-legend-lo');
  const hi     = document.getElementById('heatmap-legend-hi');
  if (legend) { title.textContent = meta.title; lo.textContent = meta.lo; hi.textContent = meta.hi; legend.style.display = 'flex'; }
}

/** Hide the floating legend panel. */
function _hideHeatmapLegend() {
  const legend = document.getElementById('heatmap-legend');
  if (legend) legend.style.display = 'none';
}

/** Mark the active metric in the dropdown and update button label. */
function _setHeatmapDropdownActive(metric) {
  ['bandwidth', 'vuln', 'stig', 'age'].forEach(m => {
    const el = document.getElementById('hm-opt-' + m);
    if (el) el.style.background = (m === metric) ? 'var(--accent, #0f3460)' : '';
  });
  const btn = document.getElementById('tb-heatmap-btn');
  if (btn) {
    if (metric) {
      const meta = _HEATMAP_META[metric] || { title: metric };
      btn.innerHTML = '\uD83C\uDF21 ' + meta.title;
      btn.classList.add('tb-btn-active');
    } else {
      btn.innerHTML = '\uD83C\uDF21 Heatmap';
      btn.classList.remove('tb-btn-active');
    }
  }
}

/** Remove all heatmap/STIG colors and restore originals. */
function clearHeatmapOverlay() {
  graph.getElements().forEach(cell => _restoreNodeColor(cell));
  graph.getLinks().forEach(link => {
    const orig = _originalLinkColors[link.id];
    if (orig) {
      link.attr('line/stroke', orig.stroke);
      if (orig.strokeWidth !== undefined) link.attr('line/strokeWidth', orig.strokeWidth);
      delete _originalLinkColors[link.id];
    }
  });
  // Clear stored metric values
  Object.keys(_heatmapValues).forEach(k => delete _heatmapValues[k]);
  _heatmapOverlayActive = null;
  _stigOverlayApplied   = false;
  _setHeatmapDropdownActive(null);
  _hideHeatmapLegend();
  setStatus('Overlay cleared.');
}

/**
 * Toggle a heatmap overlay by metric name.
 * Called from canvas.html toolbar heatmap dropdown.
 */
function toggleHeatmap(metric) {
  // If same metric is active, toggle off
  if (_heatmapOverlayActive === metric) { clearHeatmapOverlay(); return; }
  // Clear previous overlay (including STIG)
  if (_heatmapOverlayActive || _stigOverlayApplied) clearHeatmapOverlay();

  if (TOPOLOGY_ID === 'new') {
    setStatus('Save the topology first to use heatmap overlays.');
    return;
  }

  // STIG metric → open the STIG import dialog (data comes from last import)
  if (metric === 'stig') {
    openStigImportDialog();
    // Also fetch heatmap data to color from stored import history
    _fetchAndApplyHeatmap(metric);
    return;
  }

  _fetchAndApplyHeatmap(metric);
}

function _fetchAndApplyHeatmap(metric) {
  setStatus(`Loading ${metric} heatmap\u2026`);
  fetch(`${NC_BASE}/api/heatmap/${TOPOLOGY_ID}?metric=${encodeURIComponent(metric)}`)
    .then(r => r.json())
    .then(data => {
      if (data.error) { setStatus('Heatmap: ' + data.error); return; }
      const nodeVals = data.node_values || {};
      const linkVals = data.link_values || {};
      const meta = _HEATMAP_META[metric] || { fmt: v => v.toFixed(2) };

      // Color nodes and store values for tooltips
      graph.getElements().forEach(cell => {
        const val = nodeVals[cell.id];
        if (val !== undefined) {
          _applyNodeColor(cell, _heatmapColor(val));
          const label = cell.attr('label/text') || cell.attr('headerLabel/text') || cell.id;
          _heatmapValues[cell.id] = { metric, value: val, label, formatted: meta.fmt(val) };
        }
      });
      // Color links and scale stroke width (2px base → up to 6px for severity)
      graph.getLinks().forEach(link => {
        const val = linkVals[link.id];
        if (val !== undefined) {
          if (!_originalLinkColors[link.id]) {
            _originalLinkColors[link.id] = {
              stroke: link.attr('line/stroke'),
              strokeWidth: link.attr('line/strokeWidth'),
            };
          }
          link.attr('line/stroke', _heatmapColor(val));
          link.attr('line/strokeWidth', 2 + val * 4);  // 2px–6px
          const srcLabel = (link.getSourceCell() && (link.getSourceCell().attr('label/text') || '')) || '';
          const tgtLabel = (link.getTargetCell() && (link.getTargetCell().attr('label/text') || '')) || '';
          const linkLabel = srcLabel && tgtLabel ? `${srcLabel} → ${tgtLabel}` : link.id;
          _heatmapValues[link.id] = { metric, value: val, label: linkLabel, formatted: meta.fmt(val) };
        }
      });

      _heatmapOverlayActive = metric;
      _setHeatmapDropdownActive(metric);
      _showHeatmapLegend(metric);
      const n = Object.keys(nodeVals).length + Object.keys(linkVals).length;
      setStatus(`${meta.fmt === _HEATMAP_META[metric].fmt ? _HEATMAP_META[metric].title : metric} heatmap — ${n} element(s) colored.`);
    })
    .catch(err => setStatus('Heatmap error: ' + err.message));
}

/* ── Heatmap Tooltip on hover ──────────────────────────────────────────────── */
(function _initHeatmapTooltip() {
  const tip = document.getElementById('heatmap-tooltip');
  if (!tip) return;

  document.addEventListener('mousemove', function(e) {
    if (tip.style.display === 'block') {
      tip.style.left = (e.clientX + 12) + 'px';
      tip.style.top  = (e.clientY - 8)  + 'px';
    }
  });

  // Wait for paper to be initialized, then hook cell:mouseenter/mouseleave
  const _hookInterval = setInterval(function() {
    if (typeof paper === 'undefined' || !paper) return;
    clearInterval(_hookInterval);

    // Create device tooltip element
    var deviceTip = document.createElement('div');
    deviceTip.className = 'nc-device-tooltip';
    document.body.appendChild(deviceTip);

    // Device role descriptions
    var _roleDesc = {
      'router': 'Core/edge router — forwards packets between networks using routing protocols (BGP, OSPF, IS-IS). Performs inter-VLAN routing, policy-based routing, and WAN connectivity.',
      'switch': 'Network switch — provides Layer 2/3 switching for LAN segments. Handles VLAN trunking, STP, port security, and access control at the distribution or access layer.',
      'firewall': 'Security firewall — inspects and filters traffic between network zones. Enforces security policies, NAT, VPN termination, IPS/IDS, and application-layer inspection.',
      'server': 'Compute server — hosts applications, databases, or virtualization workloads. Connected to the network via redundant NICs for high availability.',
      'load_balancer': 'Application delivery controller — distributes traffic across server pools using health checks, SSL offload, persistence, and content-based routing.',
      'vpn_gateway': 'VPN concentrator — terminates encrypted tunnels (IPSec, SSL VPN) from remote sites and users. Provides secure remote access and site-to-site connectivity.',
      'wan_link': 'WAN/MPLS circuit — provides inter-site connectivity via carrier MPLS, internet, or dedicated leased line. Managed by the service provider.',
      'access_point': 'Wireless access point or controller — provides Wi-Fi coverage for end-user devices. Managed by a wireless LAN controller for centralized policy and roaming.',
      'cloud_service': 'Cloud service endpoint — represents a cloud-hosted resource (VPC, VNet, or SaaS service) connected to the enterprise network.',
      'pdu': 'Power Distribution Unit — distributes AC power from the facility feed to rack-mounted equipment via managed outlets. Supports remote monitoring, per-outlet metering, and sequential power-on.',
      'ups': 'Uninterruptible Power Supply — provides battery backup during power outages for graceful shutdown or generator switchover. Conditions power to protect against surges and sags.',
      'patch-panel': 'Patch panel — passive termination point for structured cabling. Fiber panels use LC/SC connectors for inter-rack and uplink runs. Copper panels use RJ45 Cat6a for access-layer horizontal cabling.',
    };

    paper.on('cell:mouseenter', function(cellView, evt) {
      var cell = cellView.model;
      if (!cell.isElement || !cell.isElement()) return;
      var nodeType = cell.get('nodeType') || '';
      if (nodeType === 'group-site') return;

      // Heatmap tooltip (existing)
      if (_heatmapOverlayActive) {
        var hv = _heatmapValues[cell.id];
        if (hv) {
          var color = _heatmapColor(hv.value);
          tip.innerHTML = '<span style="color:' + color + ';">\u25CF</span> <strong>' + hv.label + '</strong><br>' + hv.formatted;
          tip.style.display = 'block';
        }
        return;
      }

      // Device tooltip
      var label = cell.attr('label/text') || '';
      var ip = cell.attr('iplabel/text') || '';
      var config = cell.get('configData') || {};
      var site = config.site || config.location || '';
      var rack = config.rack || '';
      var hostname = config.hostname || label;
      var vendor = config.vendor || '';
      var model = config.model || '';
      var roleDesc = _roleDesc[nodeType] || 'Network device';
      var style = getStyle(nodeType);

      var html = '<div class="tt-header">' + _esc(label) + '</div>';
      html += '<div style="margin-bottom:6px;color:#aab;font-size:11px;font-style:italic;">' + _esc(roleDesc) + '</div>';
      html += '<div class="tt-row"><span class="tt-key">Type</span><span class="tt-val">' + _esc(style.label || nodeType) + '</span></div>';
      if (ip) html += '<div class="tt-row"><span class="tt-key">IP Address</span><span class="tt-val" style="color:#4fc3f7;">' + _esc(ip) + '</span></div>';
      if (site) html += '<div class="tt-row"><span class="tt-key">Site</span><span class="tt-val">' + _esc(site) + '</span></div>';
      if (rack) html += '<div class="tt-row"><span class="tt-key">Rack</span><span class="tt-val">' + _esc(rack) + '</span></div>';
      if (vendor) html += '<div class="tt-row"><span class="tt-key">Vendor</span><span class="tt-val">' + _esc(vendor) + '</span></div>';
      if (model) html += '<div class="tt-row"><span class="tt-key">Model</span><span class="tt-val">' + _esc(model) + '</span></div>';
      if (config.os) html += '<div class="tt-row"><span class="tt-key">OS</span><span class="tt-val">' + _esc(config.os) + '</span></div>';
      if (config.serial) html += '<div class="tt-row"><span class="tt-key">Serial</span><span class="tt-val">' + _esc(config.serial) + '</span></div>';
      if (config.sw_version) html += '<div class="tt-row"><span class="tt-key">Version</span><span class="tt-val">' + _esc(config.sw_version) + '</span></div>';
      if (config.eol_date) html += '<div class="tt-row"><span class="tt-key">EOL Date</span><span class="tt-val" style="color:#f57c00;">' + _esc(config.eol_date) + '</span></div>';
      if (config.bandwidth) html += '<div class="tt-row"><span class="tt-key">Bandwidth</span><span class="tt-val">' + _esc(config.bandwidth) + '</span></div>';

      // Imported properties (PDF/VSDX/config-import) — render any extra
      // key/value pairs not already shown above
      var _shownKeys = ['ip','site','rack','vendor','model','os','serial','sw_version','eol_date','bandwidth','hostname','label','type','interfaces','sources'];
      var extraProps = config.properties || {};
      var extraKeys = Object.keys(extraProps).filter(function(k){
        return _shownKeys.indexOf(k.toLowerCase()) === -1 && extraProps[k] != null && extraProps[k] !== '';
      });
      if (extraKeys.length > 0) {
        html += '<div style="margin-top:6px;padding-top:6px;border-top:1px solid #333;font-size:11px;">';
        html += '<strong style="color:#8899aa;">Properties:</strong>';
        extraKeys.slice(0, 10).forEach(function(k){
          html += '<div class="tt-row"><span class="tt-key">' + _esc(k) + '</span><span class="tt-val">' + _esc(String(extraProps[k])) + '</span></div>';
        });
        if (extraKeys.length > 10) html += '<div style="color:#666;">+ ' + (extraKeys.length - 10) + ' more</div>';
        html += '</div>';
      }

      // Interfaces (config-import-derived) — list of port objects
      var ifaces = config.interfaces || extraProps.interfaces;
      if (Array.isArray(ifaces) && ifaces.length > 0) {
        html += '<div style="margin-top:6px;padding-top:6px;border-top:1px solid #333;font-size:11px;">';
        html += '<strong style="color:#8899aa;">Interfaces (' + ifaces.length + '):</strong>';
        ifaces.slice(0, 8).forEach(function(i){
          var line = (i.name || '') + (i.ip ? ' [' + i.ip + ']' : '') + (i.description ? ' — ' + i.description : '');
          html += '<div style="margin:2px 0;font-family:monospace;">' + _esc(line) + '</div>';
        });
        if (ifaces.length > 8) html += '<div style="color:#666;">+ ' + (ifaces.length - 8) + ' more</div>';
        html += '</div>';
      }

      // Source diagrams — provenance from multi-PDF stitching / merge
      var sources = config.sources || extraProps.sources;
      if (Array.isArray(sources) && sources.length > 0) {
        html += '<div style="margin-top:6px;padding-top:6px;border-top:1px solid #333;font-size:11px;color:#8899aa;">';
        html += '<strong>From source(s):</strong> ' + sources.map(_esc).join(', ');
        html += '</div>';
      }

      // Port connections summary
      var links = graph.getConnectedLinks(cell);
      if (links.length > 0) {
        html += '<div style="margin-top:6px;padding-top:6px;border-top:1px solid #333;font-size:11px;color:#8899aa;">';
        html += '<strong>' + links.length + ' connection(s):</strong>';
        links.slice(0, 5).forEach(function(link) {
          var lCfg = link.get('linkConfig') || link.get('cableData') || {};
          var srcPort = lCfg.src_port || '';
          var dstPort = lCfg.dst_port || '';
          var other = link.getSourceCell();
          if (other && other.id === cell.id) other = link.getTargetCell();
          var otherLabel = other ? (other.attr('label/text') || '') : '?';
          var linkLabel = link.labels && link.labels().length ? (link.labels()[0].attrs?.text?.text || '') : '';
          html += '<div style="margin:2px 0;">\u2022 ' + _esc(linkLabel || 'link') + ' \u2192 ' + _esc(otherLabel);
          if (srcPort || dstPort) html += ' <span style="color:#666;">(' + _esc(srcPort) + ' \u2194 ' + _esc(dstPort) + ')</span>';
          html += '</div>';
        });
        if (links.length > 5) html += '<div style="color:#666;">+ ' + (links.length - 5) + ' more</div>';
        html += '</div>';
      }

      deviceTip.innerHTML = html;
      deviceTip.style.display = 'block';
      deviceTip.style.left = (evt.clientX + 16) + 'px';
      deviceTip.style.top = (evt.clientY + 16) + 'px';
    });

    paper.on('cell:mouseleave', function() {
      tip.style.display = 'none';
      deviceTip.style.display = 'none';
    });

    // Track mouse position for tooltip following
    paper.el.addEventListener('mousemove', function(evt) {
      if (deviceTip.style.display === 'block') {
        deviceTip.style.left = (evt.clientX + 16) + 'px';
        deviceTip.style.top = Math.min(evt.clientY + 16, window.innerHeight - deviceTip.offsetHeight - 10) + 'px';
      }
    });

    function _esc(s) { var d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }
  }, 200);
})();

/* ═══════════════════════════════════════════════════════════════════════════
 * STIG XCCDF/CKL Import — upload, match, overlay
 * ═══════════════════════════════════════════════════════════════════════════ */

const _STIG_COLORS = { red: '#e74c3c', yellow: '#f39c12', green: '#2ecc71' };

function openStigImportDialog() {
  if (TOPOLOGY_ID === 'new') {
    alert('Save the topology first before importing a STIG file.');
    return;
  }
  document.getElementById('stig-import-overlay').classList.remove('hidden');
  _loadStigHistory();
}

function closeStigImportDialog() {
  document.getElementById('stig-import-overlay').classList.add('hidden');
}

function _loadStigHistory() {
  const list = document.getElementById('stig-history-list');
  if (!list) return;
  list.innerHTML = '<div style="color:#7f8c8d;font-size:11px;">Loading\u2026</div>';
  fetch(`${NC_BASE}/api/compliance/${TOPOLOGY_ID}/stig-imports`)
    .then(r => r.json())
    .then(rows => {
      if (!rows.length) {
        list.innerHTML = '<div style="color:#7f8c8d;font-size:11px;">No previous imports for this topology.</div>';
        return;
      }
      list.innerHTML = rows.map(r =>
        `<div style="padding:6px 0;border-bottom:1px solid #1a2a3a;">` +
        `<div style="color:#ecf0f1;font-size:12px;font-weight:500;">${r.filename}</div>` +
        `<div style="color:#95a5a6;font-size:11px;">${r.stig_name || '\u2014'} &bull; ` +
        `${(r.format || '').toUpperCase()} &bull; ` +
        `${r.matched_hosts}/${r.total_hosts} hosts matched &bull; ` +
        `${(r.imported_at || '').split('T')[0]}</div>` +
        `</div>`
      ).join('');
    })
    .catch(() => {
      list.innerHTML = '<div style="color:#e74c3c;font-size:11px;">Failed to load history.</div>';
    });
}

function handleStigFileChange(input) {
  const name = input.files[0] ? input.files[0].name : '';
  document.getElementById('stig-file-name').textContent = name || 'No file selected';
  document.getElementById('stig-upload-btn').disabled = !input.files[0];
}

function uploadStigFile() {
  const input = document.getElementById('stig-file-input');
  const file  = input.files[0];
  if (!file) return;

  const btn    = document.getElementById('stig-upload-btn');
  const status = document.getElementById('stig-upload-status');
  btn.disabled    = true;
  btn.textContent = 'Importing\u2026';
  status.innerHTML = '<span style="color:#3498db;font-size:12px;">Parsing file and matching hostnames\u2026</span>';

  const formData = new FormData();
  formData.append('file', file);

  fetch(`${NC_BASE}/api/compliance/${TOPOLOGY_ID}/stig-import`, {
    method: 'POST',
    body: formData,
  })
    .then(r => r.json())
    .then(result => {
      btn.disabled    = false;
      btn.textContent = 'Import & Apply Overlay';
      if (result.error) {
        status.innerHTML = `<div style="color:#e74c3c;font-size:12px;padding:8px 0;">Error: ${result.error}</div>`;
        return;
      }
      _renderStigResult(result);
      applyStigOverlay(result);
      _loadStigHistory();
    })
    .catch(err => {
      btn.disabled    = false;
      btn.textContent = 'Import & Apply Overlay';
      status.innerHTML = `<div style="color:#e74c3c;font-size:12px;padding:8px 0;">Upload failed: ${err.message}</div>`;
    });
}

function _renderStigResult(result) {
  const status  = document.getElementById('stig-upload-status');
  const matched = result.matched || [];
  const unHosts = result.unmatched_hosts    || [];
  const unDevs  = result.unmatched_devices  || [];

  const red    = matched.filter(m => m.status === 'red').length;
  const yellow = matched.filter(m => m.status === 'yellow').length;
  const green  = matched.filter(m => m.status === 'green').length;

  let html = `<div style="margin:10px 0;background:#0d0d1a;border-radius:6px;padding:12px;">`;
  html += `<div style="font-size:12px;color:#ecf0f1;font-weight:600;margin-bottom:8px;">`;
  html += `${result.stig_name || 'STIG Results'} &mdash; ${matched.length} / ${result.total_hosts} hosts matched</div>`;

  // Summary badges
  html += `<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px;">`;
  html += `<span style="background:#c0392b;color:#fff;padding:3px 10px;border-radius:3px;font-size:11px;">&#x1f534; CAT I: ${red}</span>`;
  html += `<span style="background:#d68910;color:#fff;padding:3px 10px;border-radius:3px;font-size:11px;">&#x1f7e1; CAT II/III: ${yellow}</span>`;
  html += `<span style="background:#1e8449;color:#fff;padding:3px 10px;border-radius:3px;font-size:11px;">&#x1f7e2; Clean: ${green}</span>`;
  html += `</div>`;

  // Per-device table
  if (matched.length) {
    html += `<table style="width:100%;font-size:11px;color:#bdc3c7;border-collapse:collapse;margin-bottom:8px;">`;
    html += `<tr style="color:#95a5a6;border-bottom:1px solid #1a2a3a;">`;
    html += `<th style="text-align:left;padding:4px;">Device</th>`;
    html += `<th style="padding:4px;text-align:center;">CAT I</th>`;
    html += `<th style="padding:4px;text-align:center;">CAT II/III</th>`;
    html += `<th style="padding:4px;text-align:center;">Status</th>`;
    html += `</tr>`;
    matched.forEach(m => {
      const dot  = m.status === 'red' ? '#e74c3c' : m.status === 'yellow' ? '#f39c12' : '#2ecc71';
      const cat23 = (m.cat2_count || 0) + (m.cat3_count || 0);
      html += `<tr style="border-bottom:1px solid #0d1a2e;">`;
      html += `<td style="padding:4px;">${m.label}</td>`;
      html += `<td style="padding:4px;text-align:center;">${m.cat1_count || 0}</td>`;
      html += `<td style="padding:4px;text-align:center;">${cat23}</td>`;
      html += `<td style="padding:4px;text-align:center;"><span style="color:${dot};font-size:14px;">&#9679;</span></td>`;
      html += `</tr>`;
    });
    html += `</table>`;
  }

  if (unHosts.length) {
    html += `<div style="font-size:11px;color:#7f8c8d;margin-top:4px;">Unmatched hosts: ${unHosts.join(', ')}</div>`;
  }
  if (unDevs.length) {
    html += `<div style="font-size:11px;color:#7f8c8d;">Unmatched canvas devices: ${unDevs.map(d => d.label).join(', ')}</div>`;
  }

  html += `<div style="margin-top:10px;">`;
  html += `<button onclick="clearStigOverlay()" style="background:#2c3e50;border:1px solid #34495e;color:#bdc3c7;padding:5px 12px;border-radius:4px;cursor:pointer;font-size:11px;">Clear Overlay</button>`;
  html += `</div></div>`;

  status.innerHTML = html;
}

/**
 * Color canvas nodes by STIG compliance status from an import result.
 * Called automatically after a successful upload.
 */
function applyStigOverlay(result) {
  // Clear any active heatmap first, then clear prior STIG overlay
  if (_heatmapOverlayActive) clearHeatmapOverlay();
  if (_stigOverlayApplied)   clearStigOverlay();

  (result.matched || []).forEach(m => {
    const cell = graph.getCell(m.node_id);
    if (!cell) return;
    _applyNodeColor(cell, _STIG_COLORS[m.status] || '#7f8c8d');
  });

  _stigOverlayApplied = true;
  const n = (result.matched || []).length;
  setStatus(`STIG overlay applied \u2014 ${n} device(s) colored. Click \u201cClear Overlay\u201d to remove.`);
}

/** Remove STIG compliance coloring and restore original node colors. */
function clearStigOverlay() {
  if (!_stigOverlayApplied) return;
  graph.getElements().forEach(cell => _restoreNodeColor(cell));
  _stigOverlayApplied = false;
  setStatus('STIG overlay cleared.');
}

/* ═══════════════════════════════════════════════════════════════════════════
   CR Markup Mode — Visual overlay on canvas for Change Request markup
   Engineers mark elements as add (green), remove (red), modify (yellow).
   Integrates with the CR API in blueprint.py.
   ═══════════════════════════════════════════════════════════════════════════ */

// NC_BASE declared in base.html <head>
const CR_COLORS = { add: '#27ae60', remove: '#e74c3c', modify: '#f39c12' };

/** Toggle the CR Markup Mode panel and mode. */
function toggleCrMarkupMode() {
  const panel = document.getElementById('nc-cr-panel');
  const btn = document.getElementById('tb-cr-btn');
  _crModeActive = !_crModeActive;
  if (_crModeActive) {
    panel.classList.remove('hidden');
    btn.classList.add('tb-btn-cr-active');
    // Close other slide panels to avoid overlap
    document.getElementById('nc-chat-panel').classList.add('hidden');
    if (document.getElementById('netbox-panel')) document.getElementById('netbox-panel').classList.add('hidden');
    if (document.getElementById('ai-review-panel')) document.getElementById('ai-review-panel').classList.add('hidden');
    // Set full-page link
    const link = document.getElementById('cr-full-page-link');
    if (link && currentTopoId) link.href = NC_BASE + '/change-request/' + currentTopoId;
    // Load CRs for this topology
    _crLoadList();
    setStatus('CR Markup Mode active — select a CR and click elements to mark changes.');
  } else {
    panel.classList.add('hidden');
    btn.classList.remove('tb-btn-cr-active');
    _crAction = null;
    _crClearAllOverlays();
    _crUpdateActionButtons();
    setStatus('CR Markup Mode deactivated.');
  }
}

/** Load list of CRs for this topology into the dropdown. */
async function _crLoadList() {
  if (!currentTopoId || currentTopoId === 'new') return;
  try {
    const resp = await fetch(NC_BASE + '/api/change-request/' + currentTopoId + '/list');
    const data = await resp.json();
    const sel = document.getElementById('cr-select');
    const prev = sel.value;
    sel.innerHTML = '<option value="">— Select Change Request —</option>';
    (data.change_requests || []).forEach(cr => {
      const opt = document.createElement('option');
      opt.value = cr.id;
      opt.textContent = cr.title + ' (' + cr.status + ')';
      sel.appendChild(opt);
    });
    if (prev) sel.value = prev;
  } catch (e) {
    console.error('Failed to load CRs:', e);
  }
}

/** When CR dropdown changes. */
function crSelectChanged() {
  const crId = document.getElementById('cr-select').value;
  if (!crId) {
    _crActiveId = null;
    _crActiveStatus = null;
    _crItems = [];
    _crOverlays = {};
    document.getElementById('cr-active-info').classList.add('hidden');
    document.getElementById('cr-action-section').classList.add('hidden');
    document.getElementById('cr-items-section').classList.add('hidden');
    document.getElementById('cr-doc-preview').classList.add('hidden');
    _crClearAllOverlays();
    document.getElementById('cr-footer-status').textContent = 'Select or create a Change Request to begin markup.';
    return;
  }
  _crActiveId = crId;
  _crLoadCrDetails(crId);
}

/** Load CR details and items. */
async function _crLoadCrDetails(crId) {
  try {
    // Load items
    const resp = await fetch(NC_BASE + '/api/change-request/' + crId + '/items');
    const data = await resp.json();
    _crItems = data.items || [];

    // Extract status from the select label
    const sel = document.getElementById('cr-select');
    const opt = sel.querySelector('option[value="' + crId + '"]');
    const statusMatch = opt ? opt.textContent.match(/\((\w+)\)/) : null;
    _crActiveStatus = statusMatch ? statusMatch[1] : 'draft';

    // Show active CR info
    const title = opt ? opt.textContent.replace(/\s*\(\w+\)$/, '') : crId;
    document.getElementById('cr-active-title').textContent = title;
    const statusBadge = document.getElementById('cr-active-status');
    statusBadge.textContent = _crActiveStatus;
    statusBadge.className = 'cr-status-badge cr-status-' + _crActiveStatus;
    document.getElementById('cr-active-info').classList.remove('hidden');

    // Show action section only for draft CRs
    if (_crActiveStatus === 'draft') {
      document.getElementById('cr-action-section').classList.remove('hidden');
    } else {
      document.getElementById('cr-action-section').classList.add('hidden');
      _crAction = null;
      _crUpdateActionButtons();
    }

    // Show items
    document.getElementById('cr-items-section').classList.remove('hidden');
    _crRenderItems();
    _crApplyAllOverlays();

    document.getElementById('cr-footer-status').textContent =
      _crActiveStatus === 'draft'
        ? 'Pick an action, then click elements on the canvas.'
        : 'CR is ' + _crActiveStatus + ' — read-only view.';
  } catch (e) {
    console.error('Failed to load CR details:', e);
  }
}

/** Create a new CR via prompt. */
async function crCreateNew() {
  if (!currentTopoId || currentTopoId === 'new') {
    alert('Save the topology first before creating a Change Request.');
    return;
  }
  const title = prompt('Change Request title:', '');
  if (!title) return;
  const desc = prompt('Description (optional):', '');
  try {
    const resp = await fetch(NC_BASE + '/api/change-request/' + currentTopoId + '/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, description: desc || '' }),
    });
    const data = await resp.json();
    if (data.id) {
      await _crLoadList();
      document.getElementById('cr-select').value = data.id;
      crSelectChanged();
      setStatus('Change Request created: ' + title);
    } else {
      alert('Failed to create CR: ' + (data.error || 'unknown'));
    }
  } catch (e) {
    console.error('Failed to create CR:', e);
    alert('Failed to create CR: ' + e.message);
  }
}

/** Set the active markup action (add/remove/modify/null). */
function crSetAction(action) {
  _crAction = action;
  _crUpdateActionButtons();
  if (action) {
    setStatus('CR Markup: click a ' + (action === 'add' ? 'canvas element' : 'canvas element') + ' to mark as ' + action.toUpperCase() + '.');
  } else {
    setStatus('CR Markup Mode — no action selected.');
  }
}

/** Update active state of action buttons. */
function _crUpdateActionButtons() {
  ['add', 'remove', 'modify'].forEach(a => {
    const btn = document.getElementById('cr-btn-' + a);
    if (btn) btn.classList.toggle('active', _crAction === a);
  });
}

/** Handle element click in CR Markup Mode. Called from the pointerclick handler. */
function _crHandleElementClick(cell) {
  if (!_crModeActive || !_crActiveId || !_crAction || _crActiveStatus !== 'draft') return false;

  const entityId = cell.id;
  const entityType = cell.isElement() ? 'node' : 'edge';
  const entityLabel = cell.isElement()
    ? (cell.attr('label/text') || cell.get('nodeType') || entityId)
    : ('Link ' + entityId.substring(0, 8));

  // Gather element data for before_json
  const data = {};
  if (cell.isElement()) {
    data.type = cell.get('nodeType') || '';
    data.label = cell.attr('label/text') || '';
    const config = cell.get('configData') || {};
    Object.assign(data, config);
  } else {
    // Edge
    const src = cell.get('source');
    const tgt = cell.get('target');
    if (src && src.id) data.source_id = src.id;
    if (tgt && tgt.id) data.target_id = tgt.id;
    const labels = cell.labels();
    if (labels && labels.length) data.label = labels[0]?.attrs?.text?.text || '';
  }

  _crJustTarget = { cell, entityId, entityType, entityLabel, data };

  // Show justification dialog
  const dlg = document.getElementById('cr-just-dialog');
  const actionLabel = { add: 'Add', remove: 'Remove', modify: 'Modify' }[_crAction];
  const color = CR_COLORS[_crAction];
  document.getElementById('cr-just-title').innerHTML =
    '<span style="color:' + color + '">' + actionLabel + '</span> — ' + _escHtml(entityLabel);
  document.getElementById('cr-just-entity').textContent =
    entityType + ' | ' + entityId.substring(0, 12);
  document.getElementById('cr-just-text').value = '';

  // Show after-state field for modify
  const modFields = document.getElementById('cr-just-modify-fields');
  if (_crAction === 'modify') {
    modFields.classList.remove('hidden');
    document.getElementById('cr-just-after').value = '';
  } else {
    modFields.classList.add('hidden');
  }

  dlg.classList.remove('hidden');
  document.getElementById('cr-just-text').focus();
  return true; // consumed the click
}

/** Handle link click in CR Markup Mode. */
function _crHandleLinkClick(cell) {
  return _crHandleElementClick(cell);
}

/** Cancel justification dialog. */
function crJustCancel() {
  document.getElementById('cr-just-dialog').classList.add('hidden');
  _crJustTarget = null;
}

/** Confirm justification and save markup item. */
async function crJustConfirm() {
  if (!_crJustTarget || !_crActiveId) return;
  const { entityId, entityType, entityLabel, data } = _crJustTarget;
  const justification = document.getElementById('cr-just-text').value.trim();

  const payload = {
    action_type: _crAction,
    entity_id: entityId,
    entity_type: entityType,
    entity_label: entityLabel,
    justification,
    before_json: JSON.stringify(data),
  };

  if (_crAction === 'modify') {
    const afterRaw = document.getElementById('cr-just-after').value.trim();
    if (afterRaw) {
      try {
        JSON.parse(afterRaw); // validate
        payload.after_json = afterRaw;
      } catch {
        alert('After-state JSON is invalid. Please enter valid JSON.');
        return;
      }
    } else {
      // Merge before as the basis for after
      payload.after_json = JSON.stringify(data);
    }
  } else if (_crAction === 'add') {
    payload.after_json = JSON.stringify(data);
    payload.before_json = '{}';
  }

  document.getElementById('cr-just-dialog').classList.add('hidden');

  try {
    const resp = await fetch(NC_BASE + '/api/change-request/' + _crActiveId + '/markup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const result = await resp.json();
    if (result.id) {
      // Refresh items
      await _crLoadCrDetails(_crActiveId);
      setStatus('Marked ' + entityLabel + ' as ' + _crAction.toUpperCase() + '.');
    } else {
      alert('Failed to save markup: ' + (result.error || 'unknown'));
    }
  } catch (e) {
    console.error('Failed to save markup item:', e);
    alert('Failed to save markup: ' + e.message);
  }
  _crJustTarget = null;
}

/** Render the markup items list in the panel. */
function _crRenderItems() {
  const container = document.getElementById('cr-items-list');
  document.getElementById('cr-items-count').textContent = _crItems.length;

  if (_crItems.length === 0) {
    container.innerHTML = '<div style="color:var(--text-dim);font-size:12px;padding:8px;">No changes marked yet.</div>';
    return;
  }

  container.innerHTML = _crItems.map(item => {
    const action = item.action_type || 'modify';
    const label = _escHtml(item.entity_label || item.entity_id || '');
    const just = item.justification ? '<div class="mc-just">' + _escHtml(item.justification) + '</div>' : '';
    const canDelete = _crActiveStatus === 'draft';
    const delBtn = canDelete
      ? '<button class="mc-delete" onclick="crDeleteItem(\'' + item.id + '\')" title="Remove markup">&#x2715;</button>'
      : '';
    return '<div class="cr-markup-card mc-' + action + '">'
      + delBtn
      + '<span class="mc-label">' + label + '</span>'
      + '<span class="mc-badge mc-badge-' + action + '">' + action + '</span>'
      + just
      + '</div>';
  }).join('');
}

/** Delete a markup item. */
async function crDeleteItem(itemId) {
  if (!confirm('Remove this markup item?')) return;
  try {
    await fetch(NC_BASE + '/api/change-request/item/' + itemId, { method: 'DELETE' });
    await _crLoadCrDetails(_crActiveId);
    setStatus('Markup item removed.');
  } catch (e) {
    console.error('Failed to delete markup item:', e);
  }
}

/** Generate the CAB review document. */
async function crGenerateDocument() {
  if (!_crActiveId) return;
  const btn = document.getElementById('cr-gen-btn');
  btn.disabled = true;
  btn.textContent = 'Generating...';
  try {
    const resp = await fetch(NC_BASE + '/api/change-request/' + _crActiveId + '/generate', {
      method: 'POST',
    });
    const doc = await resp.json();
    if (doc.markdown) {
      document.getElementById('cr-doc-body').textContent = doc.markdown;
      document.getElementById('cr-doc-preview').classList.remove('hidden');
      setStatus('CAB document generated — ' + (doc.summary?.total || 0) + ' change(s).');
    } else {
      alert('Failed to generate document: ' + (doc.error || 'unknown'));
    }
  } catch (e) {
    console.error('Failed to generate document:', e);
    alert('Failed to generate document: ' + e.message);
  }
  btn.disabled = false;
  btn.textContent = 'Generate CAB Document';
}

/** Copy generated document markdown to clipboard. */
function crCopyDocument() {
  const body = document.getElementById('cr-doc-body').textContent;
  navigator.clipboard.writeText(body).then(() => {
    setStatus('CAB document copied to clipboard.');
  }).catch(() => {
    // Fallback
    const ta = document.createElement('textarea');
    ta.value = body;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    setStatus('CAB document copied to clipboard.');
  });
}

/** Apply visual overlays (colored glow) to all marked elements on the canvas. */
function _crApplyAllOverlays() {
  _crClearAllOverlays();
  _crOverlays = {};
  _crItems.forEach(item => {
    _crOverlays[item.entity_id] = item.action_type;
  });
  // Apply glow to matching cells
  graph.getCells().forEach(cell => {
    const action = _crOverlays[cell.id];
    if (action) {
      const view = paper.findViewByModel(cell);
      if (view) {
        view.el.classList.add('cr-overlay-' + action);
      }
    }
  });
}

/** Clear all CR visual overlays from the canvas. */
function _crClearAllOverlays() {
  graph.getCells().forEach(cell => {
    const view = paper.findViewByModel(cell);
    if (view) {
      view.el.classList.remove('cr-overlay-add', 'cr-overlay-remove', 'cr-overlay-modify');
    }
  });
  _crOverlays = {};
}

/** Escape HTML entities. */
function _escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}


/* ═══════════════════════════════════════════════════════════════════════════
 * ndc-ux-01: Canvas Search / Find Node (Ctrl+F)
 * ═══════════════════════════════════════════════════════════════════════════ */

let _searchHighlighted = [];

function _canvasSearch(query) {
  // Clear previous highlights
  _searchHighlighted.forEach(cid => {
    const cell = graph.getCell(cid);
    if (!cell) return;
    const view = paper.findViewByModel(cell);
    if (view) view.el.classList.remove('canvas-search-highlight');
  });
  _searchHighlighted = [];

  const clearBtn = document.getElementById('canvas-search-clear');
  const q = (query || '').trim().toLowerCase();

  if (!q) {
    if (clearBtn) clearBtn.style.display = 'none';
    setStatus('Ready');
    return;
  }
  if (clearBtn) clearBtn.style.display = '';

  const matches = [];
  graph.getElements().forEach(cell => {
    const cd = cell.get('configData') || {};
    const label = (cell.attr('label/text') || cell.attr('headerLabel/text') || '').toLowerCase();
    const ip       = (cd.ip       || '').toLowerCase();
    const hostname = (cd.hostname || '').toLowerCase();
    const nodeType = (cell.get('nodeType') || '').toLowerCase();
    if (label.includes(q) || ip.includes(q) || hostname.includes(q) || nodeType.includes(q)) {
      matches.push(cell);
    }
  });

  if (matches.length === 0) {
    setStatus('No matches for "' + query + '"');
    return;
  }

  matches.forEach(cell => {
    const view = paper.findViewByModel(cell);
    if (view) {
      view.el.classList.add('canvas-search-highlight');
      _searchHighlighted.push(cell.id);
    }
  });

  // Pan to first match (or fit all)
  const first = matches[0];
  const firstView = paper.findViewByModel(first);
  if (firstView) {
    const bbox = firstView.getBBox();
    const s = paper.scale().sx;
    paper.translate(
      paper.el.clientWidth  / 2 - (bbox.x + bbox.width  / 2) * s,
      paper.el.clientHeight / 2 - (bbox.y + bbox.height / 2) * s
    );
  }

  setStatus(matches.length + ' match' + (matches.length !== 1 ? 'es' : '') + ' for "' + query + '"');
}

function _clearCanvasSearch() {
  const inp = document.getElementById('canvas-search');
  if (inp) inp.value = '';
  _canvasSearch('');
}

// Ctrl+F focuses search; Escape clears it
document.addEventListener('keydown', function _searchKeydown(e) {
  if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
    const inp = document.getElementById('canvas-search');
    if (inp) { e.preventDefault(); inp.focus(); inp.select(); }
  }
  if (e.key === 'Escape') {
    const inp = document.getElementById('canvas-search');
    if (inp && document.activeElement === inp) { _clearCanvasSearch(); inp.blur(); }
  }
});


/* ═══════════════════════════════════════════════════════════════════════════
 * ndc-ux-02: Live Conflict Scanner
 * Detects duplicate IPs, duplicate hostnames; renders orange badge + panel.
 * ═══════════════════════════════════════════════════════════════════════════ */

let _conflicts = {};            // cellId -> { label, conflicts: [{type, message}] }
let _conflictScanDirty = false;

let patternAdvisor = null;

function _scheduleConflictScan() { _conflictScanDirty = true; }

function _runConflictScan() {
  if (!_conflictScanDirty) return;
  _conflictScanDirty = false;

  const elements = graph.getElements();
  const ipMap       = {};   // ip -> [cellId]
  const hostnameMap = {};   // hostname.lower -> [cellId]

  elements.forEach(cell => {
    const cd = cell.get('configData') || {};
    const ip = (cd.ip || '').trim();
    const hn = (cd.hostname || '').trim().toLowerCase();
    if (ip) { if (!ipMap[ip]) ipMap[ip] = []; ipMap[ip].push(cell.id); }
    if (hn) { if (!hostnameMap[hn]) hostnameMap[hn] = []; hostnameMap[hn].push(cell.id); }
  });

  const newConflicts = {};
  elements.forEach(cell => {
    const cd = cell.get('configData') || {};
    const ip = (cd.ip || '').trim();
    const hn = (cd.hostname || '').trim().toLowerCase();
    const label = cell.attr('label/text') || cell.get('nodeType') || cell.id;
    const conflicts = [];

    if (ip && ipMap[ip] && ipMap[ip].length > 1) {
      const peers = ipMap[ip].filter(id => id !== cell.id).map(id => {
        const c = graph.getCell(id);
        return c ? (c.attr('label/text') || c.get('nodeType') || id.slice(0, 8)) : id.slice(0, 8);
      });
      conflicts.push({ type: 'dup-ip', message: 'Duplicate IP ' + ip + ' — also on: ' + peers.join(', ') });
    }
    if (hn && hostnameMap[hn] && hostnameMap[hn].length > 1) {
      const raw = (cd.hostname || '').trim();
      const peers = hostnameMap[hn].filter(id => id !== cell.id).map(id => {
        const c = graph.getCell(id);
        return c ? (c.attr('label/text') || c.get('nodeType') || id.slice(0, 8)) : id.slice(0, 8);
      });
      conflicts.push({ type: 'dup-hostname', message: 'Duplicate hostname "' + raw + '" — also on: ' + peers.join(', ') });
    }

    if (conflicts.length) newConflicts[cell.id] = { label, conflicts };
  });

  // Sync visual badges
  const prevIds = new Set(Object.keys(_conflicts));
  const newIds  = new Set(Object.keys(newConflicts));

  prevIds.forEach(cid => {
    if (!newIds.has(cid)) {
      const v = paper.findViewByModel(graph.getCell(cid));
      if (v) v.el.classList.remove('canvas-conflict-node');
    }
  });
  newIds.forEach(cid => {
    if (!prevIds.has(cid)) {
      const v = paper.findViewByModel(graph.getCell(cid));
      if (v) v.el.classList.add('canvas-conflict-node');
    }
  });

  _conflicts = newConflicts;
  _renderConflictPanel();
}

function _renderConflictPanel() {
  const panel = document.getElementById('conflict-panel');
  const list  = document.getElementById('conflict-panel-list');
  const badge = document.getElementById('conflict-count-badge');
  if (!panel || !list) return;

  const entries = Object.entries(_conflicts);
  if (badge) badge.textContent = entries.length;

  // Drive status bar counter — always visible even when panel is dismissed
  const sbConflicts = document.getElementById('sb-conflicts');
  if (sbConflicts) {
    if (entries.length > 0) {
      sbConflicts.textContent = '⚠ ' + entries.length + ' conflict' + (entries.length !== 1 ? 's' : '');
      sbConflicts.style.display = '';
    } else {
      sbConflicts.style.display = 'none';
    }
  }

  if (entries.length === 0) { panel.classList.add('hidden'); return; }
  panel.classList.remove('hidden');

  list.innerHTML = entries.map(([cid, info]) => {
    const msgs = info.conflicts.map(c =>
      '<div class="ce-desc">' + _escHtml(c.message) + '</div>'
    ).join('');
    return '<div class="conflict-entry" onclick="_conflictGoTo(\'' + cid + '\')">' +
      '<span class="ce-icon">&#9888;</span>' +
      '<div class="ce-body"><div class="ce-title">' + _escHtml(info.label) + '</div>' + msgs + '</div>' +
      '</div>';
  }).join('');
}

function _conflictGoTo(cellId) {
  const cell = graph.getCell(cellId);
  if (!cell) return;
  const view = paper.findViewByModel(cell);
  if (!view) return;
  const bbox = view.getBBox();
  const s = paper.scale().sx;
  paper.translate(
    paper.el.clientWidth  / 2 - (bbox.x + bbox.width  / 2) * s,
    paper.el.clientHeight / 2 - (bbox.y + bbox.height / 2) * s
  );
  view.el.classList.add('canvas-search-highlight');
  setTimeout(() => view.el.classList.remove('canvas-search-highlight'), 1500);
}

function _initConflictScanner() {
  graph.on('change', _scheduleConflictScan);
  graph.on('add',    _scheduleConflictScan);
  graph.on('remove', _scheduleConflictScan);
  setInterval(_runConflictScan, 3000);
  // Initial scan 1.5s after load (let graph finish populating)
  setTimeout(() => { _conflictScanDirty = true; _runConflictScan(); }, 1500);
}

function _initPatternAdvisor() {
  patternAdvisor = new PatternAdvisor();
  // addNode / addEdge
  graph.on('add',    () => patternAdvisor.trackEdit());
  // deleteNode / deleteEdge
  graph.on('remove', () => patternAdvisor.trackEdit());
  // updateNodeProperty
  graph.on('change', () => patternAdvisor.trackEdit());
}


/* ═══════════════════════════════════════════════════════════════════════════
 * ndc-ux-03: Layer Visibility Filter (L1 / L2 / L3 toggle)
 * ═══════════════════════════════════════════════════════════════════════════ */

const _LAYER_TYPES = {
  L1: new Set([
    'media-ge','media-10ge','media-25ge','media-40ge','media-100ge','media-400ge',
    'sfp','sfp-plus','qsfp','qsfp-dd','media-fiber','media-optical','media-converter',
    'patch-panel','roadm','oadm','edfa','transponder','olt','odf'
  ]),
  L2: new Set(['switch-l2','wap','wlc']),
  L3: new Set(['router','switch-l3','firewall','load-balancer','sdwan-edge'])
};

let _layerVisibility = { L1: true, L2: true, L3: true };

function toggleLayerFilter(layer) {
  if (layer === 'all') {
    _layerVisibility = { L1: true, L2: true, L3: true };
  } else if (_LAYER_TYPES[layer]) {
    _layerVisibility[layer] = !_layerVisibility[layer];
  }
  _applyLayerFilter();
  _updateLayerButtons();
}

function _applyLayerFilter() {
  const hiddenNodeIds = new Set();

  graph.getElements().forEach(cell => {
    const nt = cell.get('nodeType') || '';
    let hidden = false;
    for (const [layer, types] of Object.entries(_LAYER_TYPES)) {
      if (types.has(nt) && !_layerVisibility[layer]) { hidden = true; break; }
    }
    const view = paper.findViewByModel(cell);
    if (view) view.el.style.display = hidden ? 'none' : '';
    if (hidden) hiddenNodeIds.add(cell.id);
  });

  // Hide/show links whose endpoints are hidden
  graph.getLinks().forEach(link => {
    const src = link.get('source');
    const tgt = link.get('target');
    const srcHidden = src && src.id && hiddenNodeIds.has(src.id);
    const tgtHidden = tgt && tgt.id && hiddenNodeIds.has(tgt.id);
    const view = paper.findViewByModel(link);
    if (view) view.el.style.display = (srcHidden || tgtHidden) ? 'none' : '';
  });
}

function _updateLayerButtons() {
  ['L1', 'L2', 'L3'].forEach(l => {
    const btn = document.getElementById('tb-layer-' + l);
    if (btn) btn.classList.toggle('tb-layer-active', !!_layerVisibility[l]);
  });
}


/* ═══════════════════════════════════════════════════════════════════════════
 * Conflict Export Guard — shows warning modal before any export when
 * conflicts exist. "Export Anyway" sets bypass flag, proceeds, then clears.
 * ═══════════════════════════════════════════════════════════════════════════ */

function _guardExport(label, fn) {
  const entries = Object.entries(_conflicts || {});
  if (entries.length === 0) { fn(); return; }

  document.getElementById('cex-sub').textContent =
    entries.length + ' conflict' + (entries.length !== 1 ? 's' : '') +
    ' found. If you proceed, the ' + label + ' will include these issues.';

  const list = document.getElementById('cex-list');
  const shown = entries.slice(0, 5);
  list.innerHTML = shown.map(([, info]) =>
    '<div class="cex-entry">&#9888; <strong>' + _escHtml(info.label) + '</strong>: ' +
    info.conflicts.map(c => _escHtml(c.message)).join('; ') + '</div>'
  ).join('') + (entries.length > 5
    ? '<div class="cex-entry cex-more">...and ' + (entries.length - 5) + ' more</div>'
    : '');

  const btn = document.getElementById('cex-proceed-btn');
  btn.onclick = () => {
    document.getElementById('conflict-export-modal').classList.add('hidden');
    _conflictExportBypassed = true;
    try { fn(); } finally { _conflictExportBypassed = false; }
  };
  document.getElementById('conflict-export-modal').classList.remove('hidden');
}


/* ═══════════════════════════════════════════════════════════════════════════
 * Board Ready Check — pre-flight gate before ERB / CCB presentation
 * ═══════════════════════════════════════════════════════════════════════════ */

function openBoardReadyCheck() {
  // Force a fresh conflict scan
  _conflictScanDirty = true;
  _runConflictScan();

  const elements = graph.getElements().filter(c => c.get('nodeType'));
  const checks = [];

  // 1 — Node count
  const nodeCount = elements.length;
  checks.push({
    label: 'Device count',
    pass: nodeCount >= 3,
    detail: nodeCount + ' device' + (nodeCount !== 1 ? 's' : '') + ' on canvas' +
            (nodeCount < 3 ? ' — need at least 3 for a meaningful design' : '')
  });

  // 2 — No conflicts
  const conflictCount = Object.keys(_conflicts).length;
  checks.push({
    label: 'No duplicate IPs / hostnames',
    pass: conflictCount === 0,
    detail: conflictCount === 0
      ? 'Clean — no duplicates detected'
      : conflictCount + ' conflict' + (conflictCount !== 1 ? 's' : '') + ' detected — click ⚠ in status bar to review'
  });

  // 3 — Routable devices have IPs
  const routables = new Set(['router','switch-l3','firewall','load-balancer','server']);
  const missingIp = elements.filter(c =>
    routables.has(c.get('nodeType')) && !(c.get('configData') || {}).ip
  );
  checks.push({
    label: 'Routable devices have IP addresses',
    pass: missingIp.length === 0,
    detail: missingIp.length === 0
      ? 'All routable devices have IPs assigned'
      : missingIp.slice(0, 3).map(c => c.attr('label/text') || c.get('nodeType')).join(', ') +
        (missingIp.length > 3 ? ' +' + (missingIp.length - 3) + ' more' : '') + ' missing IP'
  });

  // 4 — No default-label devices (renamed by engineer)
  const defaultLabelSet = new Set(Object.values(NODE_STYLES).map(s => s.label));
  const unnamed = elements.filter(c => {
    const lbl = (c.attr('label/text') || '').trim();
    return !lbl || defaultLabelSet.has(lbl);
  });
  checks.push({
    label: 'All devices have custom names',
    pass: unnamed.length === 0,
    detail: unnamed.length === 0
      ? 'All devices have been renamed'
      : unnamed.length + ' device' + (unnamed.length !== 1 ? 's' : '') +
        ' still using default labels — rename before presenting'
  });

  // 5 — Topology is saved
  const isSaved = !!(currentTopoId && currentTopoId !== 'new');
  checks.push({
    label: 'Topology saved',
    pass: isSaved,
    detail: isSaved ? 'Saved (ID: ' + currentTopoId.slice(0, 12) + '...)' : 'Unsaved changes — save before board'
  });

  const allPass = checks.every(c => c.pass);
  const failCount = checks.filter(c => !c.pass).length;

  const headline = document.getElementById('brm-headline');
  if (headline) {
    headline.textContent = allPass ? '✅ Board Ready' : '❌ ' + failCount + ' issue' + (failCount !== 1 ? 's' : '') + ' — Not Board Ready';
    headline.className = 'brm-headline ' + (allPass ? 'brm-pass' : 'brm-fail');
  }

  const list = document.getElementById('brm-list');
  if (list) {
    list.innerHTML = checks.map(c =>
      '<div class="brm-check ' + (c.pass ? 'brm-check-pass' : 'brm-check-fail') + '">' +
      '<span class="brm-icon">' + (c.pass ? '&#10003;' : '&#10007;') + '</span>' +
      '<div class="brm-check-body">' +
        '<div class="brm-check-label">' + _escHtml(c.label) + '</div>' +
        '<div class="brm-check-detail">' + _escHtml(c.detail) + '</div>' +
      '</div></div>'
    ).join('');
  }

  const atoBtn = document.getElementById('brm-ato-btn');
  if (atoBtn) atoBtn.style.display = allPass ? '' : 'none';

  document.getElementById('board-ready-modal').classList.remove('hidden');
}


/* ═══════════════════════════════════════════════════════════════════════════
 * exportCanvasPDF — opens browser print dialog (Print to PDF)
 * ═══════════════════════════════════════════════════════════════════════════ */

function exportCanvasPDF() {
  if (!_conflictExportBypassed && Object.keys(_conflicts || {}).length > 0) {
    _guardExport('PDF export', exportCanvasPDF); return;
  }
  window.print();
}


/* ═══════════════════════════════════════════════════════════════════════════
 * Config Panel Auto-Hide — slide open/close with tab handle
 * ═══════════════════════════════════════════════════════════════════════════ */

// When true, the user has explicitly collapsed the panel — don't auto-open on selection.
var _configPanelPinClosed = (function() {
  try { return localStorage.getItem('nc_props_closed') === '1'; } catch (_) { return false; }
})();

function _openConfigPanel() {
  if (_configPanelPinClosed) return; // user pinned it closed
  const p = document.getElementById('config-panel');
  const arrow = document.getElementById('cpt-arrow');
  if (p) p.classList.add('config-panel-open');
  if (arrow) arrow.innerHTML = '&#x276E;'; // ❮
}

function _closeConfigPanel() {
  const p = document.getElementById('config-panel');
  const arrow = document.getElementById('cpt-arrow');
  if (p) p.classList.remove('config-panel-open');
  if (arrow) arrow.innerHTML = '&#x276F;'; // ❯
}

function _toggleConfigPanel() {
  const p = document.getElementById('config-panel');
  if (p && p.classList.contains('config-panel-open')) {
    _configPanelPinClosed = true;
    try { localStorage.setItem('nc_props_closed', '1'); } catch (_) {}
    _closeConfigPanel();
    selectedCell = null;
    document.getElementById('config-empty').classList.remove('hidden');
    document.getElementById('config-form').classList.add('hidden');
    const lp = document.getElementById('link-config-form');
    if (lp) lp.classList.add('hidden');
    const bp = document.getElementById('bulk-edit-panel');
    if (bp) bp.classList.add('hidden');
  } else {
    _configPanelPinClosed = false;
    try { localStorage.setItem('nc_props_closed', '0'); } catch (_) {}
    _openConfigPanel();
  }
}


/* ═══════════════════════════════════════════════════════════════════════════
 * Ctrl+D — Duplicate selected node
 * ═══════════════════════════════════════════════════════════════════════════ */

function duplicateSelected() {
  if (!selectedCell || !selectedCell.isElement()) return;
  pushUndo();
  const clone = selectedCell.clone();
  const pos = selectedCell.position();
  clone.position(pos.x + 30, pos.y + 30);
  clone.set('configData', JSON.parse(JSON.stringify(selectedCell.get('configData') || {})));
  graph.addCell(clone);
  selectCell(clone);
  markDirty();
  setStatus('Duplicated — press Ctrl+D again to chain-offset');
}


/* ═══════════════════════════════════════════════════════════════════════════
 * Multi-Select (Shift+Click) + Bulk Edit
 * ═══════════════════════════════════════════════════════════════════════════ */

function _toggleMultiSelect(cell, view) {
  if (!cell.isElement()) return;
  const idx = _selectedCells.findIndex(c => c.id === cell.id);
  if (idx >= 0) {
    _selectedCells.splice(idx, 1);
    if (view) view.el.classList.remove('canvas-multi-select');
  } else {
    // Fold in the existing single-select if this is the first shift+click
    if (selectedCell && _selectedCells.length === 0) {
      const prevView = paper.findViewByModel(selectedCell);
      if (prevView) prevView.el.classList.add('canvas-multi-select');
      _selectedCells.push(selectedCell);
      selectedCell = null;
    }
    _selectedCells.push(cell);
    if (view) view.el.classList.add('canvas-multi-select');
  }
  if (_selectedCells.length === 0) {
    deselectAll();
  } else {
    _updateMultiSelectPanel();
  }
}

function _clearMultiSelect() {
  _selectedCells.forEach(c => {
    const v = paper.findViewByModel(c);
    if (v) v.el.classList.remove('canvas-multi-select');
  });
  _selectedCells = [];
  const bp = document.getElementById('bulk-edit-panel');
  if (bp) bp.classList.add('hidden');
  const bound = document.querySelector('.boundary-panel');
  if (bound) bound.style.display = '';
  const hdr = document.getElementById('config-header-title');
  if (hdr) hdr.textContent = 'Properties';
}

function _updateMultiSelectPanel() {
  const n = _selectedCells.length;
  if (n < 2) { _clearMultiSelect(); return; }

  _openConfigPanel();
  document.getElementById('config-empty').classList.add('hidden');
  document.getElementById('config-form').classList.add('hidden');
  const lp = document.getElementById('link-config-form');
  if (lp) lp.classList.add('hidden');
  const bound = document.querySelector('.boundary-panel');
  if (bound) bound.style.display = 'none';
  const bp = document.getElementById('bulk-edit-panel');
  if (bp) bp.classList.remove('hidden');

  // Summary: count by nodeType
  const typeCounts = {};
  let missingIp = 0;
  _selectedCells.forEach(c => {
    const t = c.get('nodeType') || 'unknown';
    typeCounts[t] = (typeCounts[t] || 0) + 1;
    if (!(c.get('configData') || {}).ip) missingIp++;
  });
  const typeStr = Object.entries(typeCounts).map(([t, cnt]) => cnt + '× ' + t).join(', ');
  const sumEl = document.getElementById('bulk-summary-text');
  if (sumEl) sumEl.textContent = n + ' selected: ' + typeStr;
  const misEl = document.getElementById('bulk-summary-missing');
  if (misEl) misEl.textContent = missingIp > 0 ? missingIp + ' missing IP' : '';

  const hdr = document.getElementById('config-header-title');
  if (hdr) hdr.textContent = n + ' Selected';

  // Reset input fields
  ['bulk-vlan', 'bulk-ip-prefix'].forEach(id => {
    const el = document.getElementById(id); if (el) el.value = '';
  });
  const cls = document.getElementById('bulk-classification');
  if (cls) cls.value = '';
}

function _bulkUpdate(key, val) {
  if (val === '' || val === undefined) return;
  pushUndo();
  _selectedCells.forEach(c => {
    const cfg = c.get('configData') || {};
    cfg[key] = isNaN(val) || val === '' ? val : Number(val);
    c.set('configData', cfg);
  });
  markDirty();
}

function _bulkApplyColor(colorVal) {
  pushUndo();
  _selectedCells.forEach(c => {
    const type = c.get('nodeType') || '';
    if (type === 'annotation' || type === 'callout-bubble') {
      c.attr('shape/fill', colorVal);
    } else {
      c.attr('body/fill', colorVal);
    }
    const cfg = c.get('configData') || {};
    cfg.fillColor = colorVal;
    c.set('configData', cfg);
  });
  markDirty();
}

function _bulkApplyIpPrefix() {
  const prefix = (document.getElementById('bulk-ip-prefix').value || '').trim();
  if (!prefix) { setStatus('Enter a prefix first (e.g. 10.1.0.)'); return; }
  pushUndo();
  _selectedCells.forEach((c, i) => {
    const cfg = c.get('configData') || {};
    const existing = cfg.ip || '';
    const cidr = existing.includes('/') ? '/' + existing.split('/')[1] : '/24';
    cfg.ip = prefix + (i + 1) + cidr;
    c.set('configData', cfg);
  });
  markDirty();
  _updateMultiSelectPanel();
  setStatus('Applied prefix ' + prefix + '* to ' + _selectedCells.length + ' nodes');
}

function _deleteMultiSelected() {
  const n = _selectedCells.length;
  if (n === 0) return;
  if (!confirm('Delete ' + n + ' selected node' + (n !== 1 ? 's' : '') + '?')) return;
  pushUndo();
  const cells = _selectedCells.slice();
  _selectedCells = [];
  cells.forEach(c => c.remove());
  const bp = document.getElementById('bulk-edit-panel');
  if (bp) bp.classList.add('hidden');
  _closeConfigPanel();
  markDirty();
  setStatus('Deleted ' + n + ' nodes');
}

// ── Network IaC Validate & Deploy ─────────────────────────────────────────────

function validateNetworkIaC() {
  setStatus('Validating Network IaC...');

  const elements = graph.getElements();
  const links    = graph.getLinks();
  const nodes = elements.map(el => ({
    id: el.id,
    type: el.get('nodeType') || '',
    label: el.attr('label/text') || el.attr('.label/text') || '',
    ip: el.get('ipAddress') || '',
  }));
  const edges = links.map(l => ({ source: l.getSourceElement()?.id, target: l.getTargetElement()?.id }));

  const types  = nodes.map(n => n.type.toLowerCase());
  const labels = nodes.map(n => n.label.toLowerCase());
  const nodeIds = new Set(nodes.map(n => n.id));
  const connectedIds = new Set();
  edges.forEach(e => { if(e.source) connectedIds.add(e.source); if(e.target) connectedIds.add(e.target); });

  const checks = [];

  // L1: Labels & types present
  const unlabelled = nodes.filter(n => !n.label && n.type);
  if (unlabelled.length) {
    checks.push({ id:'l1-lbl', layer:1, label:'Unlabelled nodes', status:'warn',
      message:`${unlabelled.length} node(s) have no label — cannot generate meaningful Terraform resource names`,
      fix_hint:'Double-click each unlabelled node and give it a hostname or resource name. Terraform resource names derive from node labels.',
      fix_snippet: null });
  } else {
    checks.push({ id:'l1-lbl', layer:1, label:'Node labels', status:'pass', message:`All ${nodes.length} nodes labelled` });
  }

  // L2: Edge endpoints reference valid nodes
  const badLinks = edges.filter(e => !nodeIds.has(e.source) || !nodeIds.has(e.target));
  checks.push({ id:'l2-edges', layer:2, label:'Link validity', status: badLinks.length ? 'fail' : 'pass',
    message: badLinks.length ? `${badLinks.length} link(s) have missing endpoints — delete and redraw` : `${edges.length} link(s) valid`,
    fix_hint: badLinks.length ? 'Delete broken links (hover to see red endpoints) and reconnect between valid nodes.' : null });

  // L3: Network-specific policy checks

  // Firewall / Security Group present?
  const hasFW = types.some(t => t.includes('firewall') || t.includes('fw') || t.includes('asa') || t.includes('palo') || t.includes('sg') || t.includes('acl') || t.includes('security-group') || t.includes('nsg'));
  if (!hasFW) {
    checks.push({ id:'l3-fw', layer:3, label:'Firewall / Security Group', status:'warn',
      message:'No firewall or security group node found',
      fix_hint:'Add a Firewall or Security Group node between untrusted and trusted zones. Every network boundary requires access control.',
      fix_snippet:'resource "aws_security_group" "app" {\n  name   = "app-sg"\n  vpc_id = aws_vpc.main.id\n  ingress {\n    from_port   = 443\n    to_port     = 443\n    protocol    = "tcp"\n    cidr_blocks = ["10.0.0.0/8"]\n  }\n  egress {\n    from_port   = 0\n    to_port     = 0\n    protocol    = "-1"\n    cidr_blocks = ["0.0.0.0/0"]\n  }\n}' });
  } else {
    checks.push({ id:'l3-fw', layer:3, label:'Firewall / Security Group', status:'pass', message:'Access control node found' });
  }

  // VPC / Network boundary present?
  const hasVPC = types.some(t => t.includes('vpc') || t.includes('vnet') || t.includes('vrf') || t.includes('segment') || t.includes('subnet'));
  if (nodes.length > 3 && !hasVPC) {
    checks.push({ id:'l3-vpc', layer:3, label:'Network boundary (VPC/Subnet)', status:'warn',
      message:'No VPC, VNet, or subnet node found — topology has no logical segmentation',
      fix_hint:'Add a VPC or Subnet node and group related resources inside it. Flat networks violate zero-trust segmentation.',
      fix_snippet:'resource "aws_vpc" "main" {\n  cidr_block           = "10.0.0.0/16"\n  enable_dns_hostnames = true\n  tags = { Name = "main-vpc" }\n}\nresource "aws_subnet" "private" {\n  vpc_id     = aws_vpc.main.id\n  cidr_block = "10.0.1.0/24"\n  tags = { Name = "private", Tier = "app" }\n}' });
  } else if (hasVPC) {
    checks.push({ id:'l3-vpc', layer:3, label:'Network segmentation', status:'pass', message:'VPC / subnet boundary present' });
  }

  // Any open 0.0.0.0/0 labels?
  const hasOpenWorld = labels.some(l => l.includes('0.0.0.0/0') || l.includes('any/any') || l.includes('permit any'));
  if (hasOpenWorld) {
    checks.push({ id:'l3-open', layer:3, label:'Open internet exposure', status:'fail',
      message:'Node label contains 0.0.0.0/0 or "any" — unrestricted access detected',
      fix_hint:'Replace 0.0.0.0/0 with specific CIDR ranges. Exposing all ports to the internet is a STIG CAT I finding (NET0400).',
      fix_snippet:'# Restrict to your office/VPN CIDR instead:\ningress {\n  from_port   = 443\n  to_port     = 443\n  protocol    = "tcp"\n  cidr_blocks = ["203.0.113.0/24"]  # your known IP range\n}' });
  }

  // CIDR labels on subnets?
  const subnetNodes = nodes.filter(n => n.type.toLowerCase().includes('subnet'));
  const missingCIDR = subnetNodes.filter(n => !n.label.match(/\d+\.\d+\.\d+\.\d+\/\d+/));
  if (missingCIDR.length) {
    checks.push({ id:'l3-cidr', layer:3, label:'Subnet CIDR labels', status:'warn',
      message:`${missingCIDR.length} subnet(s) have no CIDR in their label`,
      fix_hint:'Label each subnet node with its CIDR block (e.g., "10.0.1.0/24 — App Tier"). Terraform requires explicit CIDR for aws_subnet resources.',
      fix_snippet:null });
  }

  // Isolated nodes
  const isolated = nodes.filter(n => !connectedIds.has(n.id));
  if (isolated.length > 0) {
    checks.push({ id:'l3-iso', layer:3, label:'Isolated nodes', status:'warn',
      message:`${isolated.length} node(s) have no connections: ${isolated.slice(0,3).map(n=>n.label||n.type).join(', ')}`,
      fix_hint:'Connect every node to the topology. Isolated nodes produce orphaned Terraform resources with no network path.',
      fix_snippet:null });
  }

  const passed = checks.filter(c=>c.status==='pass').length;
  const failed = checks.filter(c=>c.status==='fail').length;
  const warned = checks.filter(c=>c.status==='warn').length;
  const gate   = failed === 0 ? 'PASS' : 'FAIL';
  const gateColor = gate === 'PASS' ? '#1e8449' : '#c0392b';
  const pct = checks.length > 0 ? Math.round(passed / checks.length * 100) : 100;

  let html = `<div style="text-align:center;margin:8px 0;">
    <span style="font-size:28px;font-weight:800;color:${gateColor};">${gate}</span>
    <div style="font-size:11px;color:#4a5568;">${passed} pass, ${failed} fail, ${warned} warn</div>
  </div>`;
  // progress bar
  html += `<div style="height:6px;background:#e0e0e0;border-radius:3px;margin:6px 0 10px;"><div style="height:100%;background:${gateColor};width:${pct}%;border-radius:3px;"></div></div>`;

  const fixable = checks.filter(c => (c.status==='warn'||c.status==='fail') && c.fix_hint);
  if (fixable.length) {
    html += `<div style="margin:8px 0;padding:7px 10px;background:#fef9e7;border:1px solid #f39c12;border-radius:6px;display:flex;align-items:center;justify-content:space-between;">
      <span style="font-size:11px;font-weight:600;color:#7d6608;">⚠ ${fixable.length} issue(s) have suggested fixes</span>
      <button onclick="_ndcAutoFixAll()" style="background:#d5f0e0;color:#1e7e34;border:1px solid #1e7e34;border-radius:4px;font-size:11px;padding:2px 8px;cursor:pointer;font-weight:600;">✦ Auto-fix All</button>
    </div>`;
  }

  const layerNames = {1:'Layer 1: Syntax', 2:'Layer 2: Schema', 3:'Layer 3: Network Policy'};
  [1,2,3].forEach(layer => {
    const lc = checks.filter(c=>c.layer===layer);
    if (!lc.length) return;
    html += `<div style="font-size:10px;font-weight:700;color:#4a5568;margin:8px 0 4px;text-transform:uppercase;letter-spacing:.5px;">${layerNames[layer]}</div>`;
    lc.forEach((c, idx) => {
      const icons  = {pass:'✓', fail:'✗', warn:'⚠'};
      const colors = {pass:'#1e8449', fail:'#c0392b', warn:'#b7770d'};
      const bgs    = {pass:'#f0fff4', fail:'#fff5f5', warn:'#fffbf0'};
      const rid    = `ndc-vr-${layer}-${idx}`;
      const needsAction = c.status==='warn'||c.status==='fail';
      html += `<div id="${rid}" style="margin:3px 0;padding:6px 8px;border-left:3px solid ${colors[c.status]||'#ccc'};background:${bgs[c.status]||'#f8f8f8'};border-radius:0 4px 4px 0;">`;
      html += `<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:4px;">`;
      html += `<div style="flex:1;min-width:0;"><span style="color:${colors[c.status]};font-size:13px;margin-right:4px;">${icons[c.status]||'?'}</span><b style="font-size:11px;color:#1a1a2e;">${c.label}</b><div style="font-size:10px;color:#4a5568;margin-top:2px;">${c.message}</div></div>`;
      if (needsAction) {
        html += `<div style="display:flex;flex-direction:column;gap:3px;flex-shrink:0;">`;
        if (c.fix_hint) html += `<button style="background:#dce8fb;color:#1a5276;border:1px solid #aed6f1;border-radius:3px;font-size:10px;padding:2px 7px;cursor:pointer;" onclick="_ndcToggleFix('${rid}')">Fix →</button>`;
        html += `<button style="background:transparent;color:#4a5568;border:1px solid #c9d3e0;border-radius:3px;font-size:10px;padding:2px 7px;cursor:pointer;" onclick="_ndcDismiss('${rid}')">Dismiss</button>`;
        html += `</div>`;
      }
      html += `</div>`;
      if (c.fix_hint) {
        const safeSnip = (c.fix_snippet||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        html += `<div id="${rid}-fix" style="display:none;margin-top:6px;padding:8px;background:#eaf3fb;border-radius:4px;border:1px solid #aed6f1;">
          <div style="font-size:10px;font-weight:700;color:#1a5276;margin-bottom:4px;">💡 Suggested Fix</div>
          <div style="font-size:10px;color:#2c3e50;margin-bottom:6px;">${c.fix_hint}</div>`;
        if (c.fix_snippet) {
          html += `<pre style="font-size:9px;background:#d6eaf8;padding:6px;border-radius:3px;white-space:pre-wrap;color:#1a1a2e;margin:0 0 6px;font-family:monospace;">${safeSnip}</pre>`;
          html += `<button style="background:#1a5276;color:#fff;border:none;border-radius:3px;font-size:10px;padding:2px 8px;cursor:pointer;" onclick="_ndcCopyFix(this,'${rid}')">Copy Snippet</button>`;
        }
        html += `</div>`;
      }
      html += `</div>`;
    });
  });

  html += `<p style="font-size:10px;color:#4a5568;margin-top:8px;">Network IaC validation checks topology structure and maps to Terraform VPC/subnet/SG resources.</p>`;
  window._ndcLastChecks = checks;

  // Use existing right panel if available, else alert
  if (typeof openRightPanel === 'function') openRightPanel('IaC Validation — Network', html);
  else {
    const p = document.getElementById('pc-config-panel') || document.getElementById('right-panel');
    if (p) { p.classList.add('open'); const b = p.querySelector('.pc-config-body,.right-panel-body'); if(b) b.innerHTML = html; }
  }
  setStatus(gate === 'PASS' ? '✓ Network IaC valid' : '✗ Network IaC issues — see panel');
}

window._ndcToggleFix = function(rid) { const el=document.getElementById(rid+'-fix'); if(el) el.style.display = el.style.display==='none'?'block':'none'; };
window._ndcDismiss   = function(rid) { const el=document.getElementById(rid); if(el){el.style.opacity='0.4';el.style.pointerEvents='none';} };
window._ndcCopyFix   = function(btn, rid) {
  const pre = document.getElementById(rid+'-fix')?.querySelector('pre');
  if(!pre) return;
  navigator.clipboard.writeText(pre.textContent).then(()=>{const o=btn.textContent;btn.textContent='✓ Copied!';setTimeout(()=>{btn.textContent=o;},1500);})
  .catch(()=>{const ta=document.createElement('textarea');ta.value=pre.textContent;document.body.appendChild(ta);ta.select();document.execCommand('copy');document.body.removeChild(ta);btn.textContent='✓ Copied!';setTimeout(()=>{btn.textContent='Copy Snippet';},1500);});
};
window._ndcAutoFixAll = function() {
  const checks = window._ndcLastChecks || [];
  const fixable = checks.filter(c=>(c.status==='warn'||c.status==='fail')&&c.fix_hint);
  fixable.forEach((c,idx)=>{ const rid=`ndc-vr-${c.layer}-${checks.filter(x=>x.layer===c.layer).indexOf(c)}`; window._ndcToggleFix(rid); });
  setStatus(`Showing ${fixable.length} fix snippet(s) — apply to your design, then re-validate.`);
};

function deployNetworkIaC() {
  setStatus('Generating Network Terraform bundle...');
  const elements = graph.getElements();
  const links    = graph.getLinks();
  const nodes = elements.map(el => ({
    type:  el.get('nodeType') || '',
    label: el.attr('label/text') || el.attr('.label/text') || el.get('nodeType') || 'resource',
    ip:    el.get('ipAddress') || '',
  }));

  const tfBlocks = [];
  const vpcNodes  = nodes.filter(n=>n.type.toLowerCase().includes('vpc')||n.type.toLowerCase().includes('vnet'));
  const subNets   = nodes.filter(n=>n.type.toLowerCase().includes('subnet'));
  const fwNodes   = nodes.filter(n=>n.type.toLowerCase().includes('firewall')||n.type.toLowerCase().includes('fw'));
  const sgNodes   = nodes.filter(n=>n.type.toLowerCase().includes('sg')||n.type.toLowerCase().includes('security-group')||n.type.toLowerCase().includes('nsg'));
  const routerNd  = nodes.filter(n=>n.type.toLowerCase().includes('router')||n.type.toLowerCase().includes('rtb'));

  const safe = s => (s||'resource').toLowerCase().replace(/[^a-z0-9]/g,'_').replace(/^_+|_+$/g,'');

  vpcNodes.forEach(n  => tfBlocks.push(`resource "aws_vpc" "${safe(n.label)}" {\n  cidr_block           = "${n.ip||'10.0.0.0/16'}"\n  enable_dns_hostnames = true\n  tags = { Name = "${n.label}" }\n}`));
  subNets.forEach(n   => tfBlocks.push(`resource "aws_subnet" "${safe(n.label)}" {\n  vpc_id     = aws_vpc.${safe(vpcNodes[0]?.label||'main')}.id\n  cidr_block = "${n.ip||'10.0.1.0/24'}"\n  tags = { Name = "${n.label}" }\n}`));
  sgNodes.forEach(n   => tfBlocks.push(`resource "aws_security_group" "${safe(n.label)}" {\n  name   = "${n.label}"\n  vpc_id = aws_vpc.${safe(vpcNodes[0]?.label||'main')}.id\n  egress { from_port = 0; to_port = 0; protocol = "-1"; cidr_blocks = ["0.0.0.0/0"] }\n  tags = { Name = "${n.label}" }\n}`));
  routerNd.forEach(n  => tfBlocks.push(`resource "aws_route_table" "${safe(n.label)}" {\n  vpc_id = aws_vpc.${safe(vpcNodes[0]?.label||'main')}.id\n  tags = { Name = "${n.label}" }\n}`));

  const tfContent = tfBlocks.length > 0
    ? `# Generated by ICDEV™ Network Design Canvas\n# Review and customise before applying\n\n` + tfBlocks.join('\n\n')
    : `# No mappable IaC nodes found.\n# Add VPC, Subnet, Security Group, or Router nodes to generate Terraform.`;

  const files = [
    { name: 'main.tf',      content: tfContent },
    { name: 'variables.tf', content: 'variable "region" {\n  description = "AWS region"\n  default     = "us-east-1"\n}\n' },
    { name: 'outputs.tf',   content: vpcNodes.map(n=>`output "${safe(n.label)}_id" {\n  value = aws_vpc.${safe(n.label)}.id\n}`).join('\n') || '# No outputs generated' },
    { name: 'providers.tf', content: 'terraform {\n  required_providers {\n    aws = { source = "hashicorp/aws", version = "~> 5.0" }\n  }\n}\nprovider "aws" {\n  region = var.region\n}\n' },
  ];

  let html = `<div style="text-align:center;padding:10px 0;">
    <div style="font-size:24px;color:#1e8449;">📦</div>
    <div style="color:#1e8449;font-weight:700;font-size:14px;">Network Terraform Bundle Ready</div>
    <div style="font-size:11px;color:#4a5568;margin-top:4px;">${nodes.length} nodes → ${tfBlocks.length} resource(s)</div>
  </div>`;
  html += `<div style="font-size:10px;font-weight:700;color:#4a5568;margin:8px 0 4px;text-transform:uppercase;">Generated Files</div>`;
  files.forEach(f => {
    html += `<div style="display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid #e8ecf1;">
      <span style="font-size:11px;font-family:monospace;color:#1a1a2e;">${f.name}</span>
      <button style="background:#dce8fb;color:#1a5276;border:1px solid #aed6f1;border-radius:3px;font-size:10px;padding:1px 7px;cursor:pointer;"
        onclick="_ndcPreviewFile(this,'${f.name}')">Preview</button>
    </div>`;
  });
  html += `<div id="ndc-file-preview" style="margin-top:8px;display:none;">
    <div id="ndc-preview-name" style="font-size:10px;font-weight:700;color:#4a5568;margin-bottom:4px;"></div>
    <pre id="ndc-preview-content" style="font-size:9px;background:#f4f6f9;padding:8px;border-radius:4px;white-space:pre-wrap;color:#1a1a2e;max-height:200px;overflow-y:auto;font-family:monospace;border:1px solid #c9d3e0;"></pre>
    <button style="background:#1a5276;color:#fff;border:none;border-radius:3px;font-size:10px;padding:3px 10px;cursor:pointer;margin-top:4px;" onclick="_ndcCopyPreview()">Copy</button>
  </div>`;
  html += `<button onclick="_ndcDownloadBundle()" style="width:100%;margin-top:12px;background:#1e8449;color:#fff;border:none;border-radius:4px;padding:7px;cursor:pointer;font-size:12px;font-weight:600;">⬇ Download .tf Files</button>`;
  html += `<p style="font-size:10px;color:#4a5568;margin-top:8px;">Generated Terraform targets AWS. Adapt provider block for Azure/GCP as needed.</p>`;

  window._ndcBundleFiles = files;
  if (typeof openRightPanel === 'function') openRightPanel('Deploy IaC — Network', html);
  setStatus('✓ Network Terraform bundle generated');
}

window._ndcPreviewFile = function(btn, fname) {
  const files = window._ndcBundleFiles || [];
  const f = files.find(x=>x.name===fname);
  if (!f) return;
  const preview = document.getElementById('ndc-file-preview');
  const nameEl  = document.getElementById('ndc-preview-name');
  const contEl  = document.getElementById('ndc-preview-content');
  if (!preview||!contEl) return;
  nameEl.textContent = fname;
  contEl.textContent = f.content;
  preview.style.display = 'block';
};
window._ndcCopyPreview = function() {
  const el = document.getElementById('ndc-preview-content');
  if (!el) return;
  navigator.clipboard.writeText(el.textContent).catch(()=>{
    const ta=document.createElement('textarea');ta.value=el.textContent;document.body.appendChild(ta);ta.select();document.execCommand('copy');document.body.removeChild(ta);
  });
};
window._ndcDownloadBundle = function() {
  const files = window._ndcBundleFiles || [];
  files.forEach(f => {
    const blob = new Blob([f.content], {type:'text/plain'});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = f.name;
    a.click();
    URL.revokeObjectURL(a.href);
  });
};

/* ── PatternAdvisor ──────────────────────────────────────────────────────────── */
class PatternAdvisor {
  constructor() {
    this._debounceTimer = null;
    this._debounceMs = 5000;
    this._editCount = 0;
    this._dismissed = new Map(); // findingId → _editCount at dismissal
  }

  trackEdit() {
    this._editCount++;
    if (this._debounceTimer !== null) {
      clearTimeout(this._debounceTimer);
    }
    this._debounceTimer = setTimeout(() => {
      this._debounceTimer = null;
      this.renderChips(this.analyzePatterns());
    }, this._debounceMs);
  }

  renderChips(findings) {
    const strip = document.getElementById('pattern-chips');
    if (!strip) return;
    strip.innerHTML = '';

    findings.forEach(finding => {
      if (this._dismissed.has(finding.id)) {
        const editsSince = this._editCount - this._dismissed.get(finding.id);
        if (editsSince < 5) return;
        this._dismissed.delete(finding.id);
      }

      const chip = document.createElement('div');
      chip.className = 'pattern-chip';
      chip.dataset.findingId = finding.id;

      const icon = document.createElement('span');
      icon.className = 'chip-icon';
      icon.textContent = '⚠';

      const label = document.createElement('span');
      label.className = 'chip-label';
      label.title = finding.message;
      label.textContent = finding.message;

      const showBtn = document.createElement('button');
      showBtn.className = 'chip-show-btn';
      showBtn.textContent = 'Show Me';
      showBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        this._highlightNodes(finding.nodeIds);
      });

      const dismissBtn = document.createElement('button');
      dismissBtn.className = 'chip-dismiss';
      dismissBtn.textContent = '×';
      dismissBtn.title = 'Dismiss';
      dismissBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        this._dismissed.set(finding.id, this._editCount);
        chip.remove();
      });

      chip.appendChild(icon);
      chip.appendChild(label);
      chip.appendChild(showBtn);
      chip.appendChild(dismissBtn);
      strip.appendChild(chip);
    });
  }

  _highlightNodes(nodeIds) {
    const views = nodeIds
      .map(id => { const cell = graph.getCell(id); return cell ? paper.findViewByModel(cell) : null; })
      .filter(Boolean);
    views.forEach(v => v.highlight());
    setTimeout(() => views.forEach(v => v.unhighlight()), 2000);
  }

  analyzePatterns() {
    const findings = [];
    const ENCRYPTOR_TYPES = new Set([...TYPE_SETS.TYPE1_ENCRYPTOR, ...TYPE_SETS.FIPS_ENCRYPTOR]);
    const CRITICAL_TYPES  = new Set([...TYPE_SETS.FIREWALL, ...TYPE_SETS.ROUTING]);
    const HOST_TYPES = new Set(['server', 'endpoint-pc', 'endpoint-phone', 'endpoint-iot', 'endpoint-camera']);

    const elements = graph.getElements().filter(el => {
      const t = el.get('nodeType') || '';
      return t && !/^(draw-|text-|group-|annotation|callout)/.test(t);
    });

    function nbrs(el) {
      return graph.getConnectedLinks(el).map(link => {
        const srcId = (link.get('source') || {}).id;
        const tgtId = (link.get('target') || {}).id;
        const otherId = srcId === el.id ? tgtId : srcId;
        const cell = otherId ? graph.getCell(otherId) : null;
        return cell && cell.isElement() ? cell : null;
      }).filter(Boolean);
    }

    // Rule 1: IL4+ device (requiresKG or classification >= CUI) without adjacent KG/Type-1 encryptor
    elements.forEach(el => {
      const policy = ((el.get('configData') || {}).policy) || {};
      if (!policy.requiresKG && _classRank(policy.classification || '') < 1) return;
      const hasKG = nbrs(el).some(n => TYPE_SETS.TYPE1_ENCRYPTOR.has(n.get('nodeType') || ''));
      if (!hasKG) {
        findings.push({
          id: `il4-no-kg-${el.id}`,
          type: 'warning',
          message: `"${el.attr('label/text') || el.id}" is IL4+ but has no KG/Type-1 encryptor connected`,
          nodeIds: [el.id],
        });
      }
    });

    // Rule 2: Routable host with no gateway (router) connected
    elements.forEach(el => {
      if (!HOST_TYPES.has(el.get('nodeType') || '')) return;
      const hasGW = nbrs(el).some(n => TYPE_SETS.ROUTING.has(n.get('nodeType') || ''));
      if (!hasGW) {
        findings.push({
          id: `no-gateway-${el.id}`,
          type: 'warning',
          message: `"${el.attr('label/text') || el.id}" is a routable device with no gateway (router) connected`,
          nodeIds: [el.id],
        });
      }
    });

    // Rule 3: Critical node (firewall/router) with only a single uplink — no redundancy
    elements.forEach(el => {
      if (!CRITICAL_TYPES.has(el.get('nodeType') || '')) return;
      if (graph.getConnectedLinks(el).length === 1) {
        findings.push({
          id: `single-uplink-${el.id}`,
          type: 'warning',
          message: `Critical node "${el.attr('label/text') || el.id}" has only a single uplink — no redundancy`,
          nodeIds: [el.id],
        });
      }
    });

    // Rule 4: Firewall with no adjacent IDS/IPS sensor
    elements.forEach(el => {
      if (!TYPE_SETS.FIREWALL.has(el.get('nodeType') || '')) return;
      const hasIdsIps = nbrs(el).some(n => {
        const lbl = n.attr('label/text') || '';
        return /\bids\b|\bips\b|sensor/i.test(lbl) || /^(ids|ips)$/.test(n.get('nodeType') || '');
      });
      if (!hasIdsIps) {
        findings.push({
          id: `fw-no-ids-${el.id}`,
          type: 'warning',
          message: `Firewall "${el.attr('label/text') || el.id}" has no IDS/IPS sensor connected (SI-4)`,
          nodeIds: [el.id],
        });
      }
    });

    // Rule 5: DMZ node directly connected to internal/trusted zone without a firewall between
    elements.forEach(el => {
      const policy = ((el.get('configData') || {}).policy) || {};
      const isDmz = policy.zone === 'dmz' || /\bdmz\b/i.test(el.attr('label/text') || '');
      if (!isDmz) return;
      const internalNeighbors = nbrs(el).filter(n => {
        if (TYPE_SETS.FIREWALL.has(n.get('nodeType') || '')) return false;
        const npol = ((n.get('configData') || {}).policy) || {};
        return npol.zone === 'trusted' || npol.zone === 'management' || npol.zone === 'internal';
      });
      if (internalNeighbors.length > 0) {
        findings.push({
          id: `dmz-direct-internal-${el.id}`,
          type: 'warning',
          message: `DMZ node "${el.attr('label/text') || el.id}" is directly connected to internal zone without a firewall (SC-7)`,
          nodeIds: [el.id, ...internalNeighbors.map(n => n.id)],
        });
      }
    });

    // Rule 6: Cross-domain link without an encryptor at either endpoint
    graph.getLinks().forEach(link => {
      const srcId = (link.get('source') || {}).id;
      const tgtId = (link.get('target') || {}).id;
      if (!srcId || !tgtId) return;
      const src = graph.getCell(srcId);
      const tgt = graph.getCell(tgtId);
      if (!src || !tgt || !src.isElement() || !tgt.isElement()) return;
      const srcCls = _nodeClassification(src);
      const tgtCls = _nodeClassification(tgt);
      if (!srcCls || !tgtCls || _classRank(srcCls) === _classRank(tgtCls)) return;
      if (ENCRYPTOR_TYPES.has(src.get('nodeType') || '') || ENCRYPTOR_TYPES.has(tgt.get('nodeType') || '')) return;
      findings.push({
        id: `cross-domain-no-enc-${link.id}`,
        type: 'warning',
        message: `Cross-domain link "${src.attr('label/text') || srcId}" (${srcCls}) ↔ "${tgt.attr('label/text') || tgtId}" (${tgtCls}) has no encryptor (CA-3, SC-8)`,
        nodeIds: [srcId, tgtId],
      });
    });

    return findings;
  }
}
