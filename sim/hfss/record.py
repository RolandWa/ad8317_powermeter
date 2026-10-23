# ----------------------------------------------
# Script Recorded by Ansys Electronics Desktop Version 2026.1.2
# 17:38:15  Aug 12, 2026
# ----------------------------------------------
import ScriptEnv
ScriptEnv.Initialize("Ansoft.ElectronicsDesktop")
oDesktop.RestoreWindow()
oProject = oDesktop.SetActiveProject("SMA_Coupon_MS")
oDesign = oProject.SetActiveDesign("SMA_Coupon_MS_4L")
oEditor = oDesign.SetActiveEditor("3D Modeler")
oEditor.Rotate(
	[
		"NAME:Selections",
		"Selections:="		, "2023R1_HRMG_300_468B2",
		"NewPartsModelFlag:="	, "Model"
	], 
	[
		"NAME:RotateParameters",
		"RotateAxis:="		, "Y",
		"RotateAngle:="		, "90deg"
	])
oEditor.Rotate(
	[
		"NAME:Selections",
		"Selections:="		, "2023R1_HRMG_300_468B2",
		"NewPartsModelFlag:="	, "Model"
	], 
	[
		"NAME:RotateParameters",
		"RotateAxis:="		, "X",
		"RotateAngle:="		, "90deg"
	])
oEditor.Mirror(
	[
		"NAME:Selections",
		"Selections:="		, "2023R1_HRMG_300_468B2",
		"NewPartsModelFlag:="	, "Model"
	], 
	[
		"NAME:MirrorParameters",
		"MirrorBaseX:="		, "-0.02mm",
		"MirrorBaseY:="		, "60mm",
		"MirrorBaseZ:="		, "0mm",
		"MirrorNormalX:="	, "-1mm",
		"MirrorNormalY:="	, "0mm",
		"MirrorNormalZ:="	, "0mm"
	])
oEditor.Move(
	[
		"NAME:Selections",
		"Selections:="		, "2023R1_HRMG_300_468B2",
		"NewPartsModelFlag:="	, "Model"
	], 
	[
		"NAME:TranslateParameters",
		"TranslateVectorX:="	, "-89.96mm",
		"TranslateVectorY:="	, "-60mm",
		"TranslateVectorZ:="	, "-1.5902mm"
	])
oEditor.Rotate(
	[
		"NAME:Selections",
		"Selections:="		, "2023R1_HRMG_300_468B1",
		"NewPartsModelFlag:="	, "Model"
	], 
	[
		"NAME:RotateParameters",
		"RotateAxis:="		, "X",
		"RotateAngle:="		, "90deg"
	])
oEditor.Rotate(
	[
		"NAME:Selections",
		"Selections:="		, "2023R1_HRMG_300_468B1",
		"NewPartsModelFlag:="	, "Model"
	], 
	[
		"NAME:RotateParameters",
		"RotateAxis:="		, "Z",
		"RotateAngle:="		, "90deg"
	])
oEditor.Move(
	[
		"NAME:Selections",
		"Selections:="		, "2023R1_HRMG_300_468B1",
		"NewPartsModelFlag:="	, "Model"
	], 
	[
		"NAME:TranslateParameters",
		"TranslateVectorX:="	, "-28.4273mm",
		"TranslateVectorY:="	, "30mm",
		"TranslateVectorZ:="	, "-1.5902mm"
	])
oEditor.Move(
	[
		"NAME:Selections",
		"Selections:="		, "Port_Sheet_P2",
		"NewPartsModelFlag:="	, "Model"
	], 
	[
		"NAME:TranslateParameters",
		"TranslateVectorX:="	, "-51.89mm",
		"TranslateVectorY:="	, "0mm",
		"TranslateVectorZ:="	, "-3.1629mm"
	])
oEditor.Move(
	[
		"NAME:Selections",
		"Selections:="		, "Port_Sheet_P1",
		"NewPartsModelFlag:="	, "Model"
	], 
	[
		"NAME:TranslateParameters",
		"TranslateVectorX:="	, "-38.12mm",
		"TranslateVectorY:="	, "5mm",
		"TranslateVectorZ:="	, "-1.55520000000001mm"
	])
oEditor.Move(
	[
		"NAME:Selections",
		"Selections:="		, "Port_Sheet_P1",
		"NewPartsModelFlag:="	, "Model"
	], 
	[
		"NAME:TranslateParameters",
		"TranslateVectorX:="	, "-29.52mm",
		"TranslateVectorY:="	, "-5.45999999999999mm",
		"TranslateVectorZ:="	, "-1.6077mm"
	])
oProject.Save()
oProject.Save()
oModule = oDesign.GetModule("MeshSetup")
oModule.Reassign("Mesh_Transition", 
	[
		"Objects:="		, ["MeshZone_Trans_Left","Sub_Pre1"]
	])
oProject.Save()
oProject.Save()
oModule.InitialMeshSettings(
	[
		"NAME:MeshSettings",
		[
			"NAME:GlobalSurfApproximation",
			"CurvedSurfaceApproxChoice:=", "UseSlider",
			"SliderMeshSettings:="	, 5
		],
		[
			"NAME:GlobalCurvilinear",
			"Apply:="		, False
		],
		[
			"NAME:GlobalModelRes",
			"UseAutoLength:="	, True
		],
		"MeshMethod:="		, "Auto",
		"UseLegacyFaceterForTauVolumeMesh:=", False,
		"DynamicSurfaceResolution:=", False,
		"UseFlexMeshingForTAUvolumeMesh:=", False,
		"UseAlternativeMeshMethodsAsFallBack:=", True,
		"AllowPhiForLayeredGeometry:=", True
	])
oProject.Save()
oDesign.GenerateMesh(["HFSS_Adaptive"])
oProject.Save()
oDesign.AnalyzeAllNominal()
