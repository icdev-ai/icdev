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
let saveTimer = null;
let isDirty = false;

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
};

function getStyle(type) {
  return NODE_STYLES[type] || { fill: '#1a1a2e', stroke: '#7a8cb0', label: type, symbol: '?' };
}

/* ── Cisco Traditional Stencils — filled SVG shapes (48x48 viewBox) ────────── */
// Each entry: { fill: body path, detail: white internal strokes/paths }
// Rendered as: solid colored body shape + white detail lines on top
const CISCO_STENCILS = {
  // Router: classic Cisco circle with cross arrows
  'router': {
    body: 'M24,4 a20,20 0 1,0 0.01,0 Z',
    detail: 'M24,14 v20 M14,24 h20 M17,17 l-7,-7 M16.5,10.5 l-6.5,1.5 M10.5,16.5 l1.5,-6.5 M31,17 l7,-7 M31.5,10.5 l6.5,1.5 M37.5,16.5 l-1.5,-6.5 M17,31 l-7,7 M10.5,31.5 l1.5,6.5 M16.5,37.5 l-6.5,-1.5 M31,31 l7,7 M37.5,31.5 l-1.5,6.5 M31.5,37.5 l6.5,-1.5',
  },
  // Switch L2: rectangle with bidirectional arrows
  'switch-l2': {
    body: 'M6,14 h36 v20 h-36 Z M2,24 l6,-5 v10 Z M46,24 l-6,-5 v10 Z',
    detail: 'M14,24 h8 M28,24 h-8 M18,21 l-4,3 l4,3 M30,21 l4,3 l-4,3',
  },
  // Switch L3: rectangle with arrows + vertical lines (taller)
  'switch-l3': {
    body: 'M6,14 h36 v20 h-36 Z M2,24 l6,-5 v10 Z M46,24 l-6,-5 v10 Z',
    detail: 'M14,24 h8 M28,24 h-8 M18,21 l-4,3 l4,3 M30,21 l4,3 l-4,3 M24,10 v4 M24,34 v4',
  },
  // Firewall: brick wall with flame
  'firewall': {
    body: 'M6,8 h36 v32 h-36 Z',
    detail: 'M6,16 h36 M6,24 h36 M6,32 h36 M18,8 v8 M30,8 v8 M12,16 v8 M24,16 v8 M36,16 v8 M18,24 v8 M30,24 v8 M12,32 v8 M24,32 v8 M36,32 v8',
  },
  // Server: rack unit
  'server': {
    body: 'M10,6 h28 v36 h-28 Z',
    detail: 'M10,14 h28 M10,22 h28 M10,30 h28 M32,9 a1.5,1.5 0 1,0 0.01,0 M32,17 a1.5,1.5 0 1,0 0.01,0 M32,25 a1.5,1.5 0 1,0 0.01,0 M32,33 a1.5,1.5 0 1,0 0.01,0',
  },
  // Load Balancer: circle with balance scales
  'load-balancer': {
    body: 'M24,4 a20,20 0 1,0 0.01,0 Z',
    detail: 'M24,12 v22 M14,18 h20 M14,18 l-2,8 h8 l-2,-8 M34,18 l-2,8 h8 l-2,-8',
  },
  // WAP: antenna with waves
  'wap': {
    body: 'M16,38 h16 l-2,-4 h-12 Z',
    detail: 'M24,34 v-18 M24,16 l-3,-3 M24,16 l3,-3 M18,20 a10,10 0 0,1 12,0 M14,16 a16,16 0 0,1 20,0 M10,12 a22,22 0 0,1 28,0',
  },
  // Cloud
  'cloud': {
    body: 'M12,34 a10,10 0 0,1 -2,-18 a12,12 0 0,1 20,-6 a10,10 0 0,1 12,8 a8,8 0 0,1 -4,16 Z',
    detail: '',
  },
  // Patch Panel: panel with ports
  'patch-panel': {
    body: 'M4,16 h40 v16 h-40 Z',
    detail: 'M10,20 v8 M16,20 v8 M22,20 v8 M28,20 v8 M34,20 v8 M40,20 v8',
  },
  // Meet-Me Room: building
  'meet-me-room': {
    body: 'M8,40 V16 L24,6 L40,16 V40 Z',
    detail: 'M18,40 V26 h12 V40 M14,20 h4 v4 h-4 Z M30,20 h4 v4 h-4 Z',
  },
  // Cross-Connect: patch panel with X cables
  'cross-connect': {
    body: 'M4,14 h40 v20 h-40 Z',
    detail: 'M12,14 v20 M22,14 v20 M32,14 v20 M8,18 l8,12 M16,18 l-8,12 M26,18 l8,12 M34,18 l-8,12',
  },
  // SIEM: monitor with eye
  'siem': {
    body: 'M6,8 h36 v28 h-36 Z M16,40 h16 M20,36 v4 M28,36 v4',
    detail: 'M14,22 a10,6 0 1,1 20,0 a10,6 0 1,1 -20,0 M24,19 a3,3 0 1,0 0.01,0',
  },
  // ROADM: hexagon
  'roadm': {
    body: 'M14,8 h20 l10,16 l-10,16 h-20 l-10,-16 Z',
    detail: 'M18,24 h12 M24,18 v12',
  },
  // Transponder: diamond
  'transponder': {
    body: 'M24,6 l18,18 l-18,18 l-18,-18 Z',
    detail: 'M18,24 h12 M24,18 v12',
  },
  // Endpoint PC: monitor
  'endpoint-pc': {
    body: 'M8,8 h32 v24 h-32 Z M18,36 h12 M16,32 v4 M32,32 v4 M14,40 h20',
    detail: 'M12,12 h24 v16 h-24 Z',
  },
  // IP Phone
  'endpoint-phone': {
    body: 'M12,6 h24 v36 h-24 Z',
    detail: 'M16,10 h16 v8 h-16 Z M16,22 h4 v3 h-4 Z M22,22 h4 v3 h-4 Z M28,22 h4 v3 h-4 Z M16,27 h4 v3 h-4 Z M22,27 h4 v3 h-4 Z M28,27 h4 v3 h-4 Z M16,32 h4 v3 h-4 Z M22,32 h4 v3 h-4 Z M28,32 h4 v3 h-4 Z',
  },
  // IoT Device: chip
  'endpoint-iot': {
    body: 'M14,14 h20 v20 h-20 Z',
    detail: 'M18,14 v-6 M24,14 v-6 M30,14 v-6 M18,34 v6 M24,34 v6 M30,34 v6 M14,18 h-6 M14,24 h-6 M14,30 h-6 M34,18 h6 M34,24 h6 M34,30 h6 M20,20 h8 v8 h-8 Z',
  },
  // Camera
  'endpoint-camera': {
    body: 'M8,16 h32 v20 h-32 Z',
    detail: 'M24,26 a6,6 0 1,0 0.01,0 M24,26 a2,2 0 1,0 0.01,0 M30,14 l6,-4 v6',
  },
  // SD-WAN Edge
  'sdwan-edge': {
    body: 'M4,24 a20,12 0 1,0 40,0 a20,12 0 1,0 -40,0 Z',
    detail: 'M16,20 h16 M16,24 h16 M16,28 h16',
  },
  // POP
  'pop': {
    body: 'M8,38 V14 L24,6 L40,14 V38 Z',
    detail: 'M8,14 L24,6 L40,14 M16,20 h16 v14 h-16 Z',
  },
};

// Map device type to stencil (with fallback matching)
function getCiscoStencil(type) {
  if (CISCO_STENCILS[type]) return CISCO_STENCILS[type];
  if (type.startsWith('switch-l3')) return CISCO_STENCILS['switch-l3'];
  if (type.startsWith('switch')) return CISCO_STENCILS['switch-l2'];
  if (type.includes('router') || type === 'mpls-pe' || type === 'mpls-p' || type === 'route-reflector') return CISCO_STENCILS['router'];
  if (type.includes('firewall') || type.includes('fw') || type.includes('nfw')) return CISCO_STENCILS['firewall'];
  if (type.includes('server') || type.includes('srv') || type.includes('historian')) return CISCO_STENCILS['server'];
  if (type.includes('balancer') || type.includes('lb') || type.includes('alb') || type.includes('nlb')) return CISCO_STENCILS['load-balancer'];
  if (type.includes('cloud') || type.includes('vpc') || type.includes('vnet') || type.includes('vcn')) return CISCO_STENCILS['cloud'];
  if (type.includes('wap') || type === 'wlc') return CISCO_STENCILS['wap'];
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
      transform: 'translate(15, 0) scale(1.67)',  // Scale 48→80px, centered in 110w
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
      refX: '50%', refY: '100%',
      textAnchor: 'middle',
      dy: 4,
      fontSize: 10,
      fontFamily: 'Segoe UI, system-ui, sans-serif',
      fill: '#eaeaea',
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
    drawGrid: { name: 'dot', args: { color: 'rgba(255,255,255,0.06)' } },
    background: { color: 'transparent' },
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

  // Selection
  paper.on('element:pointerclick', (view) => selectCell(view.model));
  paper.on('link:pointerclick', (view) => selectCell(view.model));
  paper.on('blank:pointerclick', () => deselectAll());

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
    if (e.key === 'Delete' || e.key === 'Backspace') {
      if (selectedCell && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA') {
        deleteSelected();
      }
    }
  });

  // Canvas node hover tooltips
  initCanvasTooltips();

  updateStatusBar();
  initColorPalettes();
  setStatus('Ready — drag objects from the palette to begin');
}

/* ── Canvas Tooltips ──────────────────────────────────────────────────────────── */
function initCanvasTooltips() {
  // Create tooltip element
  const tooltip = document.createElement('div');
  tooltip.id = 'canvas-tooltip';
  tooltip.className = 'canvas-tooltip';
  document.body.appendChild(tooltip);

  paper.on('element:mouseenter', (view, evt) => {
    const cell = view.model;
    const type = cell.get('nodeType') || 'unknown';
    const label = cell.attr('label/text') || '';
    const config = cell.get('configData') || {};
    const style = getStyle(type);

    let html = `<strong>${label}</strong><br><span class="tt-type">${style.label} (${type})</span>`;
    if (config.ip) html += `<br>IP: ${config.ip}`;
    if (config.asn) html += `<br>ASN: ${config.asn}`;
    if (config.protocol) html += `<br>Protocol: ${config.protocol}`;
    if (config.mtu && config.mtu !== '1500') html += `<br>MTU: ${config.mtu}`;
    if (config.local_pref && config.local_pref !== '100') html += `<br>LOCAL_PREF: ${config.local_pref}`;
    if (config.ospf_area) html += `<br>OSPF Area: ${config.ospf_area}`;
    if (config.vlan) html += `<br>VLAN: ${config.vlan}`;
    if (config.vrf) html += `<br>VRF: ${config.vrf}`;

    tooltip.innerHTML = html;
    tooltip.style.display = 'block';
    tooltip.style.left = (evt.clientX + 12) + 'px';
    tooltip.style.top = (evt.clientY + 12) + 'px';
  });

  paper.on('element:mouseleave', () => {
    tooltip.style.display = 'none';
  });

  // Link hover tooltip
  paper.on('link:mouseenter', (view, evt) => {
    const link = view.model;
    const protocol = link.get('protocol') || '';
    const labels = link.labels();
    const linkLabel = labels.length ? labels[0].attrs?.text?.text || '' : '';
    const src = graph.getCell(link.get('source')?.id);
    const tgt = graph.getCell(link.get('target')?.id);
    const srcLabel = src?.attr?.('label/text') || '?';
    const tgtLabel = tgt?.attr?.('label/text') || '?';

    let html = `<strong>${srcLabel} → ${tgtLabel}</strong>`;
    if (linkLabel) html += `<br>Label: ${linkLabel}`;
    if (protocol) html += `<br>Protocol: ${protocol}`;

    // Cable run annotation
    const cable = link.get('cableData') || {};
    if (cable.cable_type) html += `<br><span class="tt-cable-label">Cable:</span> ${cable.cable_type}`;
    if (cable.distance_m) html += `<br><span class="tt-cable-label">Distance:</span> ${cable.distance_m}m`;
    if (cable.conduit_id) html += `<br><span class="tt-cable-label">Conduit:</span> ${cable.conduit_id}`;
    if (cable.fiber_strands) html += `<br><span class="tt-cable-label">Strands:</span> ${cable.fiber_strands}`;

    tooltip.innerHTML = html;
    tooltip.style.display = 'block';
    tooltip.style.left = (evt.clientX + 12) + 'px';
    tooltip.style.top = (evt.clientY + 12) + 'px';
  });

  paper.on('link:mouseleave', () => {
    tooltip.style.display = 'none';
  });
}

/* ── Create a node on the canvas ─────────────────────────────────────────────── */
function createNode(type, x, y, label, nodeId, configData) {
  const style = getStyle(type);
  const displayLabel = label || style.label;
  const config = configData || {};

  // Drawing shapes
  if (style.shape && ['rect','roundedrect','circle','ellipse','diamond','triangle','hexagon','star','hline','vline','arrow'].includes(style.shape)) {
    const isLine = ['hline','vline','arrow'].includes(style.shape);
    const w = config._width || (isLine ? (style.shape === 'vline' ? 20 : 160) : (['circle','star'].includes(style.shape) ? 80 : 120));
    const h = config._height || (isLine ? (style.shape === 'hline' || style.shape === 'arrow' ? 20 : 100) : (['circle','star'].includes(style.shape) ? 80 : 80));
    const shapePath = drawingShapePath(style.shape, w, h);

    // Label position: inside top-left for large zones, centered for small shapes
    const isLargeZone = w > 150 || h > 100;
    const labelAttrs = isLargeZone
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
          stroke: config._stroke || style.stroke,
          strokeWidth: config._strokeWidth || 2,
          fillOpacity: config._fillOpacity || 0.6,
        },
        label: {
          text: isLine ? '' : (displayLabel === style.label ? '' : displayLabel),
          fill: config._textColor || (config._stroke || style.stroke),
          ...labelAttrs,
        }
      }
    });
    node.set('nodeType', type);
    node.set('configData', config);
    graph.addCell(node);
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
    return node;
  }

  // Group/site containers are larger
  const isGroup = (type === 'group-site');
  const w = isGroup ? 300 : 110;
  const h = isGroup ? 200 : 70;

  // Try Cisco traditional stencil (filled shape + white detail)
  const stencil = getCiscoStencil(type);

  const node = new NetworkNode({
    id: nodeId || joint.util.uuid(),
    position: { x: x || 100, y: y || 100 },
    size: { width: w, height: h },
    attrs: {
      body: {
        fill: isGroup ? (config._fill || style.fill) : 'transparent',
        stroke: isGroup ? (config._stroke || style.stroke) : 'transparent',
        strokeWidth: isGroup ? 2 : 0,
        rx: 6, ry: 6,
      },
      stencilGroup: stencil ? {
        transform: 'translate(15, 2) scale(1.67)',
      } : { display: 'none' },
      stencilBody: stencil ? {
        d: stencil.body,
        fill: config._fill || style.stroke,  // Use stroke color as stencil fill
      } : { d: '' },
      stencilDetail: stencil ? {
        d: stencil.detail || '',
        stroke: '#ffffff',
        strokeWidth: 1.5,
      } : { d: '' },
      symbol: {
        text: stencil ? '' : style.symbol,
        fill: config._stroke || style.stroke,
      },
      label: {
        text: displayLabel,
        fill: config._textColor || '#eaeaea',
      }
    }
  });

  node.set('nodeType', type);
  node.set('configData', config);
  graph.addCell(node);
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
      attrs: { text: { text: label, fill: '#7a8cb0', fontSize: 10, fontFamily: 'Cascadia Code, Consolas, monospace' } },
      position: 0.5
    }] : [],
    // Curved routing for tunnels
    ...(style && style.curve ? { connector: { name: 'smooth' } } : {}),
  });
  link.set('protocol', protocol || '');
  graph.addCell(link);
  return link;
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
  const style = getStyle(type);
  const node = createNode(type, x, y, style.label);
  selectCell(node);
  markDirty();
  updateStatusBar();
}

/* ── Selection & Config Panel ───────────────────────────────────────────────── */
function selectCell(cell) {
  selectedCell = cell;

  if (cell.isElement()) {
    const type = cell.get('nodeType') || 'unknown';
    const label = cell.attr('label/text') || '';
    const config = cell.get('configData') || {};

    document.getElementById('config-empty').classList.add('hidden');
    document.getElementById('config-form').classList.remove('hidden');

    document.getElementById('cfg-type').value = type;
    document.getElementById('cfg-label').value = label;
    document.getElementById('cfg-ip').value = config.ip || '';
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

    // Highlight on paper
    paper.findViewByModel(cell)?.highlight();
  } else {
    // Link selected
    document.getElementById('config-empty').classList.remove('hidden');
    document.getElementById('config-form').classList.add('hidden');
  }
}

function deselectAll() {
  selectedCell = null;
  document.getElementById('config-empty').classList.remove('hidden');
  document.getElementById('config-form').classList.add('hidden');
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
      const edgeObj = {
        id: cell.id,
        source: cell.get('source').id || '',
        target: cell.get('target').id || '',
        label: cell.labels().length ? cell.labels()[0].attrs?.text?.text || '' : '',
        protocol: cell.get('protocol') || ''
      };
      const cableData = cell.get('cableData');
      if (cableData && Object.keys(cableData).length) {
        edgeObj.cableData = cableData;
      }
      edges.push(edgeObj);
    }
  });

  return { nodes, edges };
}

function loadGraphJSON(data) {
  graph.clear();
  const nodes = data.nodes || [];
  const edges = data.edges || [];

  nodes.forEach(n => {
    createNode(n.type, n.x, n.y, n.label, n.id, n.config);
  });
  edges.forEach(e => {
    if (e.source && e.target) {
      const link = createLink(e.source, e.target, e.label, e.protocol, e.id);
      if (e.cableData && link) {
        link.set('cableData', e.cableData);
      }
    }
  });
  updateStatusBar();
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
      await fetch(NC_BASE + `/api/topologies/${currentTopoId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ graph_json: gj })
      });
    }
    isDirty = false;
    const now = new Date().toLocaleTimeString();
    document.getElementById('sb-saved').textContent = `Saved at ${now}`;
    setStatus('Saved');
  } catch (err) {
    setStatus('Save failed: ' + err.message);
  }
}

/* ── API: Load ────────────────────────────────────────────────────────────────── */
async function loadTopology(id) {
  setStatus('Loading...');
  try {
    const r = await fetch(NC_BASE + `/api/topologies/${id}`);
    if (!r.ok) throw new Error('Not found');
    const data = await r.json();
    if (data.graph_json) loadGraphJSON(data.graph_json);
    document.getElementById('topo-name-display').textContent = data.name || 'Untitled';
    setStatus('Loaded — ' + data.name);
    isDirty = false;
  } catch (err) {
    setStatus('Load failed: ' + err.message);
  }
}

/* ── Export ───────────────────────────────────────────────────────────────────── */
async function exportAs(fmt) {
  if (!currentTopoId || currentTopoId === 'new') {
    await saveTopology();
  }
  if (!currentTopoId || currentTopoId === 'new') {
    alert('Save the topology first before exporting.');
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

/* ── New canvas ───────────────────────────────────────────────────────────────── */
function newCanvas() {
  if (isDirty && !confirm('You have unsaved changes. Start a new canvas?')) return;
  pushUndo();
  graph.clear();
  currentTopoId = 'new';
  document.getElementById('topo-name-display').textContent = 'Untitled Topology';
  window.history.replaceState({}, '', '/canvas/new');
  deselectAll();
  updateStatusBar();
  isDirty = false;
  setStatus('New canvas — drag objects from the palette');
}

/* ── Status helpers ───────────────────────────────────────────────────────────── */
function markDirty() {
  isDirty = true;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveTopology, 3000); // autosave after 3s idle
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
  if (btn) btn.classList.toggle('tb-btn-active', fenceMode);
  if (fenceMode) {
    setStatus('Fence mode ON — click and drag to select devices, then choose classification');
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
  zone.style.borderColor = b.color;
  zone.style.backgroundColor = b.color;
  zone.style.setProperty('--fence-opacity', b.fill_opacity || 0.08);

  // Classification label badge
  const badge = document.createElement('div');
  badge.className = 'boundary-label';
  badge.style.backgroundColor = b.color;
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
});
