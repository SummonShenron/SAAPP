import React, { useState, useEffect, useRef } from 'react';
import { api } from '../api';
import './__styles__/SelfService.css';

interface DocumentRecord {
  id: string;
  filename: string;
  uploadDate: string;
  fileSize: string;
}

export const SelfServicePage: React.FC = () => {
  const principal = localStorage.getItem('principal') ?? "";
  
  // --- STATE LAYER ---
  const [allowedAffiliates, setAllowedAffiliates] = useState<string[]>([]);
  const [userGroups, setUserGroups] = useState<string[]>([]);
  const [selectedAffiliate, setSelectedAffiliate] = useState<string>('');
  const [loadingInitial, setLoadingInitial] = useState<boolean>(true);

  // Card 2 State (Upload)
  const [selectedFiles, setSelectedFiles] = useState<FileList | null>(null);
  const [uploading, setUploading] = useState<boolean>(false);
  const [uploadStatus, setUploadStatus] = useState<{ success: boolean; message: string } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Card 3 State (Manage / Delete)
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [fetchingDocs, setFetchingDocs] = useState<boolean>(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  // --- DERIVED SECURITY STATE ---
  const isGlobalAdmin = userGroups.includes('Global_Admins');
  
  // FIX: Only check for the Ingesters role if a valid affiliate is selected.
  // This prevents searching for " Ingesters" (empty string prefix) when the component initializes.
  const hasIngestPermission = isGlobalAdmin || (selectedAffiliate && userGroups.includes(`${selectedAffiliate} Ingesters`));

  // --- LIFECYCLE: FETCH INITIAL PERMISSIONS & ALL GROUPS ---
  useEffect(() => {
    const initPermissions = async () => {
      try {
        console.log("Fetching groups for:", principal);
        const affiliates = await api.getAffiliates(principal);
        const profile = await api.getUserGroups(principal) as any;
        
        // Handle both raw array or object { groups: [...] }
        const verifiedGroups = Array.isArray(profile)
            ? profile
            : (profile && Array.isArray(profile.groups) ? profile.groups : []); 
            
        setUserGroups(verifiedGroups);
        setAllowedAffiliates(affiliates);
        
        if (affiliates.length > 0) {
            setSelectedAffiliate(affiliates[0]); 
        }
      } catch (err) {
        console.error("Failed loading user authorization directory:", err);
      } finally {
        setLoadingInitial(false);
      }
    };
    initPermissions();
  }, [principal]);

  // --- LIFECYCLE: RE-FETCH DOCUMENTS WHEN TARGET AFFILIATE CHANGES ---
  useEffect(() => {
    if (!selectedAffiliate) return;

    const loadIndexedDocuments = async () => {
      setFetchingDocs(true);
      try {
        const docs = await api.getIngestedDocuments(principal, selectedAffiliate);
        setDocuments(docs);
      } catch (err) {
        console.error("Error loading active file directories:", err);
        setDocuments([]);
      } finally {
        setFetchingDocs(false);
      }
    };

    loadIndexedDocuments();
  }, [selectedAffiliate, principal]);

  // --- HANDLERS ---
  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      setSelectedFiles(e.target.files);
      setUploadStatus(null);
    }
  };

  const handleUploadSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedFiles || !selectedAffiliate || !hasIngestPermission) return;

    setUploading(true);
    setUploadStatus(null);

    try {
      await api.uploadDocuments(principal, selectedAffiliate, selectedFiles);
      setUploadStatus({ success: true, message: `Successfully staged ${selectedFiles.length} item(s) for ingestion workflow.` });
      setSelectedFiles(null);
      if (fileInputRef.current) fileInputRef.current.value = '';
      
      // Refresh manifest view
      const updatedDocs = await api.getIngestedDocuments(principal, selectedAffiliate);
      setDocuments(updatedDocs);
    } catch (err: any) {
      setUploadStatus({ success: false, message: err.message || "Pipeline ingest processing exception." });
    } finally {
      setUploading(false);
    }
  };

  const handleDeleteDocument = async (docId: string, filename: string) => {
    if (!hasIngestPermission) return;
    if (!window.confirm(`Are you certain you want to purge "${filename}" from the vector memory space? This step is permanent.`)) return;
    
    setDeletingId(docId);
    try {
      await api.deleteDocument(principal, selectedAffiliate, docId);
      setDocuments(prev => prev.filter(doc => doc.id !== docId));
    } catch (err) {
      console.error("Core index expulsion failure:", err);
      alert("Failed to safely prune target file elements from vector indices.");
    } finally {
      setDeletingId(null);
    }
  };

  const filteredDocuments = documents.filter(doc => 
    doc.filename.toLowerCase().includes(searchQuery.toLowerCase())
  );

  if (loadingInitial) {
    return <div className="self-service-loading">Checking security claims directory profiles...</div>;
  }

  return (
    <div className="self-service-container">
      <header className="self-service-header">
        <h1>Multi-Tenant Ingestion Gateway</h1>
      </header>

      <div className="vertical-card-stack">
        
        {/* CARD 1: TARGET BOUNDARY CONFIGURATION */}
        <section className="service-card boundary-card">
          <div className="card-badge">STEP 1</div>
          <h2>Select Target Knowledge Base</h2>
          <p className="card-description">Choose which organizational security perimeter you are authorized to provision text data structures into.</p>
          
          <div className="form-group">
            <label htmlFor="affiliate-target">Active Isolation Context:</label>
            <select
              id="affiliate-target"
              value={selectedAffiliate}
              onChange={(e) => {
                setSelectedAffiliate(e.target.value);
                setUploadStatus(null); // Clear lingering context status messages
                setSelectedFiles(null);
              }}
              disabled={uploading || deletingId !== null}
            >
              {allowedAffiliates.map(aff => (
                <option key={aff} value={aff}>{aff.replace('_', ' ')}</option>
              ))}
            </select>
          </div>
        </section>

        {/* CARD 2: RAW DOCUMENT PROVISIONING ZONE */}
        <section className={`service-card upload-card ${!hasIngestPermission ? 'read-only-lock' : ''}`}>
          <div className="card-badge">STEP 2</div>
          <h2>Ingest Pipeline Processing</h2>
          <p className="card-description">Stage files for localized formatting, parsing, vector embedding splitting, and metadata pinning.</p>
          
          {!hasIngestPermission ? (
            <div className="security-warning-lockout">
              ⚠️ Content staging locked. Your account directory tokens lack elevated <strong>{selectedAffiliate} Ingesters</strong> roles.
            </div>
          ) : (
            <form onSubmit={handleUploadSubmit} className="upload-form-layout">
              <div className="file-input-wrapper">
                <input 
                  type="file" 
                  id="file-selector"
                  multiple 
                  accept=".pdf"
                  onChange={handleFileChange}
                  ref={fileInputRef}
                  disabled={uploading}
                />
                <label htmlFor="file-selector" className="custom-file-button">
                  {selectedFiles ? `Selected (${selectedFiles.length} files tracked)` : "Choose PDF Files"}
                </label>
                {selectedFiles && (
                  <ul className="staged-files-preview">
                    {Array.from(selectedFiles).map((file, idx) => (
                      <li key={idx}>📄 {file.name} ({(file.size / 1024).toFixed(1)} KB)</li>
                    ))}
                  </ul>
                )}
              </div>

              <button 
                type="submit" 
                className="action-button upload-submit-btn" 
                disabled={!selectedFiles || uploading}
              >
                {uploading ? "Executing Chunk Splitting Ingestion..." : `Upload to ${selectedAffiliate.replace('_', ' ')}`}
              </button>
            </form>
          )}

          {uploadStatus && (
            <div className={`status-banner ${uploadStatus.success ? 'success' : 'error'}`}>
              {uploadStatus.message}
            </div>
          )}
        </section>

        {/* CARD 3: AUDIT AND PURGE CONTROLS */}
        <section className="service-card management-card">
          <div className="card-badge">STEP 3</div>
          <h2>Document Manifest & Vector Exclusions</h2>
          <p className="card-description">Search, query, and cleanly delete documents currently sitting within this isolated security boundary index.</p>
          
          <div className="search-bar-wrapper">
            <input 
              type="text" 
              placeholder="Filter active index by filename..." 
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="manifest-search-input"
            />
          </div>

          {fetchingDocs ? (
            <div className="loader-subtext">Synchronizing vector cluster indices...</div>
          ) : filteredDocuments.length === 0 ? (
            <div className="empty-manifest-notice">
              {searchQuery ? "No files match query criteria." : `No documents indexed inside ${selectedAffiliate.replace('_', ' ')}.`}
            </div>
          ) : (
            <div className="manifest-table-scroll-zone">
              <table className="manifest-table">
                <thead>
                  <tr>
                    <th>Asset Identifier Name</th>
                    <th>Committed Timestamp</th>
                    <th>Allocation</th>
                    {hasIngestPermission && <th style={{ textAlign: 'center' }}>Purge Action</th>}
                  </tr>
                </thead>
                <tbody>
                  {filteredDocuments.map((doc) => (
                    <tr key={doc.id}>
                      <td className="doc-name-cell">📄 {doc.filename}</td>
                      <td>{new Date(doc.uploadDate).toLocaleString()}</td>
                      <td>{doc.fileSize}</td>
                      {hasIngestPermission && (
                        <td style={{ textAlign: 'center' }}>
                          <button
                            onClick={() => handleDeleteDocument(doc.id, doc.filename)}
                            className="delete-row-btn"
                            disabled={deletingId !== null}
                            title="Purge Document Vectors"
                          >
                            {deletingId === doc.id ? "Pruning..." : "Delete"}
                          </button>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

      </div>
    </div>
  );
};